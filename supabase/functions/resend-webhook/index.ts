// Resend webhook receiver, deployed as a Supabase Edge Function so it stays
// online 24/7 regardless of whether the laptop running Flask is on.
//
// Flow:
//   Resend  ─POST signed event─►  this function
//                                    │
//                                    │ verify Svix signature
//                                    ▼
//                          insert into email_events_raw
//                                    │
//                  (later) Local Flask polls table, dispatches event,
//                          marks processed_at = now().
//
// Secrets to set after deploying:
//   supabase secrets set RESEND_WEBHOOK_SECRET=whsec_...
//
// The function uses SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY which Supabase
// injects automatically into every Edge Function runtime — no extra setup.

import { Webhook } from "npm:svix@1.42.0";
import { createClient } from "jsr:@supabase/supabase-js@2";

const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const SUPABASE_SERVICE_ROLE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
const WEBHOOK_SECRET = Deno.env.get("RESEND_WEBHOOK_SECRET") ?? "";

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });

Deno.serve(async (req: Request): Promise<Response> => {
  // Health check — Resend dashboard sometimes probes the URL.
  if (req.method === "GET" || req.method === "HEAD") {
    return json({
      ok: true,
      endpoint: "resend-webhook",
      method_required: "POST",
      verification: WEBHOOK_SECRET ? "svix" : "disabled",
    });
  }
  if (req.method !== "POST") {
    return new Response("Method Not Allowed", { status: 405 });
  }

  const raw = await req.text();
  let payload: Record<string, unknown> = {};

  if (WEBHOOK_SECRET) {
    try {
      const wh = new Webhook(WEBHOOK_SECRET);
      const headers: Record<string, string> = {};
      req.headers.forEach((v, k) => (headers[k.toLowerCase()] = v));
      payload = wh.verify(raw, headers) as Record<string, unknown>;
    } catch (e) {
      console.warn("svix verification failed:", e);
      return json({ error: "invalid signature" }, 401);
    }
  } else {
    try {
      payload = JSON.parse(raw);
    } catch {
      payload = {};
    }
  }

  const eventType = (payload["type"] as string) ??
    (payload["event"] as string) ?? "";
  const data = (payload["data"] as Record<string, unknown>) ?? {};
  const resendId = (data["email_id"] as string) ?? (data["id"] as string) ??
    null;

  const supa = createClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY);
  const { error } = await supa.from("email_events_raw").insert({
    resend_id: resendId,
    event_type: eventType,
    payload,
  });

  if (error) {
    console.error("insert into email_events_raw failed:", error);
    return json({ error: error.message }, 500);
  }
  return json({ ok: true, event: eventType, resend_id: resendId });
});
