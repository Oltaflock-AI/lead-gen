# Home Services — Cold Email Offer Config

> Niche: Home Services (plumbers, electricians, HVAC, roofing, landscaping, cleaning, pest control, handyman, painting, flooring)
> Channel: Cold email
> Goal: Book a 10-minute demo call
> Last updated: 2026-05

---

## 1. Core Offer (Same Across All Countries)

**Product:** AI Voice Agent + Missed-Call SMS Recovery for home service businesses.

**What it does:**
- Answers every inbound call 24/7 with a local-accent voice
- Triages by service type and urgency (emergency vs scheduled)
- Books straight into the prospect's calendar/CRM
- If caller hangs up before pickup, instant SMS within 15 seconds
- Logs every lead with full conversation history

**Pricing offer (the hook that makes saying no feel stupid):**
- $0 setup, $0 monthly, $0 contract
- Pay only when the agent books a job — never before
- 30-day "no revenue, $100 back" guarantee: if it does not generate at least one booked job in the first 30 days, we refund $100 of our own money on top of you owing nothing
- One after-hours callout typically pays for 5 to 10 times the per-job fee

**Risk-reversal phrasing to use verbatim or paraphrase:**
- "You don't pay a cent until we book your next job."
- "If we don't book a single job in 30 days, I send you $100 of my own money. You still owe nothing."
- "Zero setup, zero monthly, zero contract. Aligned incentives or no deal."

**Why this beats every competitor in market:**
Existing tools (InstantLead, Rosie, AI Support Brisbane, etc.) charge subscription fees regardless of results. Our offer flips the risk entirely: aligned incentives, zero downside, $100 back if we underdeliver.

---

## 2. Email Framework (Same Skeleton — Variables Swap by Country)

**Subject line patterns** (pick one based on tone needed):
- Outcome-led: `How many calls did you miss {{today_or_yesterday}}?`
- Loss-framed: `You're missing 1 in 4 calls — here's the fix`
- Direct/blunt: `${{currency_symbol}}0 until we book your first job`
- Curiosity: `Built something for {{business_name}} — worth 10 mins?`

**Body structure (5 blocks, in order):**

1. **Pattern interrupt opener** — name the prospect's specific reality (under a sink, on a roof, mid-job)
2. **Cost of the problem** — localized stat about missed calls in their country
3. **Solution in one sentence** — voice agent + SMS recovery, country-localized voice
4. **The offer** — pay-per-booked-job, zero risk
5. **CTA** — 10-minute demo, low friction

**Hard rules:**
- Max 150 words for cold version, max 220 for warm/data-backed version
- Never use buzzwords ("revolutionary", "cutting-edge", "leverage")
- Never use em dashes (use commas, periods, or parentheses instead)
- Never imply experiences Khush hasn't had
- Always sign off as "Khush, OltaFlock AI"
- Use the prospect's local terminology (see country sections below)

---

## 3. Personalization Variables

These get filled from prospect data before sending:

| Variable | Source | Example |
|----------|--------|---------|
| `{{first_name}}` | Prospect record | "Dave" |
| `{{business_name}}` | Prospect record | "Dave's Plumbing" |
| `{{city}}` or `{{suburb}}` | Prospect record | "Brisbane" / "Pittsburgh" |
| `{{business_type}}` | Niche selector | "plumber", "electrician", etc. |
| `{{country}}` | Country selector | "AU", "US", "UK", etc. |
| `{{trade_term}}` | Country mapping | "tradie" (AU), "contractor" (US) |
| `{{currency_symbol}}` | Country mapping | "$", "£", "€", "₹" |
| `{{job_value_range}}` | Country + business type mapping | "$150–$400" |
| `{{emergency_value}}` | Country + business type mapping | "$500–$1,500" |
| `{{voice_accent}}` | Country mapping | "Australian", "American", "British" |
| `{{common_crm}}` | Country + business type mapping | "ServiceM8", "Jobber", "Housecall Pro" |
| `{{specific_pain}}` | Business type mapping | "burst pipe", "blown fuse", "blocked gutter" |
| `{{secondary_pain}}` | Business type mapping | "blocked drain", "tripped breaker" |

---

## 4. Country-Specific Personalization

### 4.1 Australia (AU)

- **Voice/accent:** Australian (non-negotiable — US accents get hung up on)
- **Currency:** AUD ($)
- **Terminology:** "tradie", "ute", "after-hours", "call-out"
- **Common CRMs:** ServiceM8 (dominant), Tradify, AroFlo, simPRO
- **Compliance hook:** Mention Australian Privacy Act 1988 + Australian data residency in demo (don't bury in email)
- **Job value ranges (per booked job):**
  - Plumber: $150–$400 standard, $500–$1,500 emergency after-hours
  - Electrician: $180–$450 standard, $400–$1,200 emergency
  - HVAC: $200–$600 standard, $500–$1,500 emergency
  - Roofing: $300–$800 inspection, $1,500–$10,000+ repair
  - Landscaping: $80–$300/job
  - Cleaning: $150–$400 commercial, $80–$200 residential
  - Pest control: $150–$350
  - Handyman: $80–$250
  - Painting: $400–$3,000
  - Flooring: $500–$5,000
- **Localized stat to use:** "Tradies miss 27% of calls on average, up to 62% for solo operators" + "80% of callers don't leave a voicemail — they ring the next {{business_type}} on Google"
- **Per-booked-job price:** $40–$60 AUD
- **Cultural tone:** Direct, no-bullshit, dry. Aussie tradies hate being sold to. Lead with the math, skip the hype.
- **Phrases to use:** "straight up", "no worries", "after-hours", "missed jobs"
- **Phrases to avoid:** "synergies", "scale your business", "transform your operations"

### 4.2 United States (US)

- **Voice/accent:** American (regional neutral — no strong Southern/NY accent)
- **Currency:** USD ($)
- **Terminology:** "contractor", "service tech", "service call", "dispatch"
- **Common CRMs:** Jobber, Housecall Pro, ServiceTitan (enterprise), FieldEdge
- **Compliance hook:** TCPA-compliant SMS (opt-in language), HIPAA mention only for medical-adjacent
- **Job value ranges (per booked job):**
  - Plumber: $150–$500 standard, $500–$2,000 emergency
  - Electrician: $200–$600 standard, $500–$1,500 emergency
  - HVAC: $250–$800 standard, $800–$3,500 emergency
  - Roofing: $400–$1,200 inspection, $5,000–$25,000 repair/replace
  - Landscaping: $100–$400/job
  - Cleaning: $200–$600 commercial, $120–$300 residential
  - Pest control: $150–$400
  - Handyman: $100–$350
  - Painting: $500–$5,000
  - Flooring: $800–$8,000
- **Localized stat to use:** "Home service businesses miss 27% of calls on average. Speed-to-lead is decisive — first contractor to respond wins the job 78% of the time"
- **Per-booked-job price:** $50–$75 USD
- **Cultural tone:** Outcome-led, ROI-focused. Americans respond to numbers and growth language.
- **Phrases to use:** "booked jobs", "revenue", "ROI", "competitive edge"
- **Phrases to avoid:** "tradie" (not used), "ute" (confusing)

### 4.3 United Kingdom (UK)

- **Voice/accent:** British (neutral RP or mild regional)
- **Currency:** GBP (£)
- **Terminology:** "tradesman", "engineer" (for boiler/HVAC), "callout", "Gas Safe registered" (plumbers/HVAC)
- **Common CRMs:** Commusoft, Joblogic, simPRO, Tradify
- **Compliance hook:** UK GDPR + Data Protection Act 2018, ICO-compliant
- **Job value ranges:**
  - Plumber: £80–£250 standard, £250–£700 emergency
  - Electrician: £100–£300 standard, £200–£600 emergency
  - HVAC/boiler engineer: £150–£500 standard, £300–£900 emergency
  - Roofing: £200–£800 inspection, £2,000–£15,000 repair
  - Landscaping: £80–£300/job
  - Cleaning: £100–£400 commercial, £60–£200 residential
  - Pest control: £100–£300
  - Handyman: £60–£200
  - Painting/decorating: £300–£3,000
  - Flooring: £500–£5,000
- **Localized stat to use:** "UK tradesmen miss roughly a quarter of inbound calls. Most callers don't leave a message — they ring the next {{business_type}} on the list"
- **Per-booked-job price:** £35–£50 GBP
- **Cultural tone:** Understated, dry humor works, avoid hype. Brits respond to "sensible" and "practical."
- **Phrases to use:** "callout", "sensible", "fair pricing", "Gas Safe"
- **Phrases to avoid:** "crushing it", "10x", "revolutionary"

### 4.4 Canada (CA)

- **Voice/accent:** Canadian English (close to neutral US, no strong regional)
- **Currency:** CAD ($)
- **Terminology:** "contractor", "trades", "service call"
- **Common CRMs:** Jobber (Canadian-built, very common), Housecall Pro, ServiceTitan
- **Compliance hook:** PIPEDA, Canadian data residency
- **Job value ranges:**
  - Plumber: $150–$450 standard, $400–$1,500 emergency
  - Electrician: $180–$500 standard, $400–$1,200 emergency
  - HVAC: $200–$700 standard, $700–$3,000 emergency
  - Roofing: $400–$1,200 inspection, $4,000–$20,000 repair
  - Landscaping: $100–$400/job
  - Cleaning: $180–$500 commercial, $100–$280 residential
  - Pest control: $150–$350
  - Handyman: $100–$300
  - Painting: $400–$4,000
  - Flooring: $700–$7,000
- **Localized stat to use:** Same as US (data overlaps), but reference "Canadian contractors" specifically
- **Per-booked-job price:** $50–$70 CAD
- **Cultural tone:** Polite, less aggressive than US. Lead with practicality, not hype.
- **Phrases to use:** "fair", "reliable", "sensible"
- **Phrases to avoid:** Hard-sell US-style language

### 4.5 New Zealand (NZ)

- **Voice/accent:** New Zealand English (NZ accent — not Australian, customers will notice the difference)
- **Currency:** NZD ($)
- **Terminology:** "tradie" (shared with AU), "sparkie" (electrician), "after-hours"
- **Common CRMs:** Tradify (NZ-built, dominant), simPRO, AroFlo
- **Compliance hook:** NZ Privacy Act 2020
- **Job value ranges:**
  - Plumber: $120–$350 standard, $400–$1,200 emergency
  - Electrician: $150–$400 standard, $350–$1,000 emergency
  - HVAC: $180–$550 standard, $450–$1,300 emergency
  - Roofing: $300–$900 inspection, $2,500–$15,000 repair
  - Landscaping: $80–$300/job
  - Cleaning: $130–$400 commercial, $70–$200 residential
  - Pest control: $130–$300
  - Handyman: $70–$220
  - Painting: $350–$2,800
  - Flooring: $500–$4,500
- **Localized stat to use:** Same as AU (markets are similar), reference "Kiwi tradies"
- **Per-booked-job price:** $40–$55 NZD
- **Cultural tone:** Even more low-key than Aussie. Modesty wins. Don't oversell.
- **Phrases to use:** "sweet as", "no worries", "give it a go"
- **Phrases to avoid:** US hype language, anything that sounds boastful

### 4.6 India (IN)

- **Voice/accent:** Indian English (Hindi/regional language support if requested)
- **Currency:** INR (₹)
- **Terminology:** "service provider", "vendor", "AMC" (Annual Maintenance Contract)
- **Common CRMs:** Less standardized — many use WhatsApp + Excel, some use Zoho/HouseJoy
- **Compliance hook:** DPDP Act 2023
- **Job value ranges (much lower than Western markets):**
  - Plumber: ₹300–₹1,500 standard, ₹800–₹3,000 emergency
  - Electrician: ₹400–₹1,800 standard, ₹1,000–₹3,500 emergency
  - HVAC/AC service: ₹500–₹2,500 service, ₹3,000–₹15,000 repair
  - Carpenter: ₹500–₹2,000/job
  - Cleaning: ₹500–₹2,500/job
  - Pest control: ₹800–₹3,500
  - Painting: ₹15,000–₹80,000 full home
- **Localized stat to use:** Reference WhatsApp dependency — "Most leads come via WhatsApp and never get a response after 8pm"
- **Per-booked-job price:** ₹100–₹200 INR (volume game, not margin game)
- **Cultural tone:** Relationship-led, more deferential opening, but still direct on the offer.
- **Strategic note:** Volume model, not margin model. Push WhatsApp integration heavily — it's the dominant lead channel. Voice agent value is lower, SMS/WhatsApp recovery value is higher.
- **Phrases to use:** "AMC", "WhatsApp", "service provider"

### 4.7 Germany (DE)

- **Voice/accent:** German (Hochdeutsch, no strong regional)
- **Currency:** EUR (€)
- **Terminology:** "Handwerker", "Notdienst" (emergency service), "Meisterbetrieb"
- **Common CRMs:** Streamline, Craftnote, ToolTime (DE-built), simPRO
- **Compliance hook:** GDPR + BDSG (German federal data protection), EU data residency
- **Job value ranges:**
  - Klempner (plumber): €100–€350 standard, €300–€900 Notdienst
  - Elektriker: €120–€400 standard, €300–€800 Notdienst
  - Heizung/HVAC: €150–€500 standard, €400–€1,200 Notdienst
  - Dachdecker (roofer): €200–€700 inspection, €3,000–€20,000 repair
  - Garten- und Landschaftsbau: €100–€400/job
  - Reinigung: €120–€500 commercial, €80–€250 residential
  - Schädlingsbekämpfung: €120–€350
  - Hausmeister/handyman: €70–€220
  - Maler: €400–€4,000
- **Localized stat to use:** German emphasis on Pünktlichkeit (punctuality) and Notdienst. "70% of Notdienst calls go unanswered after 18:00."
- **Per-booked-job price:** €40–€60 EUR
- **Cultural tone:** Formal Sie unless prospect indicates otherwise. Precision matters more than warmth. Lead with technical accuracy.
- **Phrases to use:** "Notdienst", "Meisterbetrieb", "DSGVO-konform"
- **Phrases to avoid:** Excessive friendliness, US-style hype, casual "du"

### 4.8 France (FR)

- **Voice/accent:** French (Île-de-France neutral)
- **Currency:** EUR (€)
- **Terminology:** "artisan", "dépannage" (emergency callout), "intervention"
- **Common CRMs:** Organilog, Kizeo, Sage, Praxedo
- **Compliance hook:** RGPD + CNIL, EU data residency
- **Job value ranges:**
  - Plombier: €80–€300 standard, €250–€800 dépannage
  - Électricien: €100–€350 standard, €280–€700 dépannage
  - Chauffagiste: €120–€450 standard, €350–€1,000 dépannage
  - Couvreur (roofer): €200–€700 inspection, €2,500–€18,000 repair
  - Paysagiste: €80–€350/job
  - Nettoyage: €100–€450 commercial, €70–€220 residential
  - Désinsectisation: €100–€320
  - Bricoleur: €60–€200
  - Peintre: €350–€3,500
- **Localized stat to use:** "Les artisans manquent en moyenne 1 appel sur 4. 80% des clients ne laissent pas de message — ils appellent le suivant."
- **Per-booked-job price:** €40–€55 EUR
- **Cultural tone:** Formal "vous" by default. French prospects appreciate intellectual framing of the problem before the solution. Don't rush to the pitch.
- **Phrases to use:** "dépannage", "intervention rapide", "RGPD-conforme"

### 4.9 Spain (ES)

- **Voice/accent:** Castilian Spanish (or Latin American neutral if pitching multinational)
- **Currency:** EUR (€)
- **Terminology:** "fontanero", "electricista", "urgencias", "presupuesto"
- **Common CRMs:** Sage, STEL Order, Holded, Praxedo
- **Compliance hook:** GDPR + LOPDGDD, EU data residency
- **Job value ranges:**
  - Fontanero: €60–€250 standard, €200–€700 urgencias
  - Electricista: €80–€300 standard, €250–€650 urgencias
  - Climatización: €100–€400 standard, €300–€900 urgencias
  - Tejados/cubiertas: €180–€650 inspection, €2,000–€15,000 repair
  - Jardinería: €60–€280/job
  - Limpieza: €80–€400 commercial, €60–€180 residential
  - Control de plagas: €100–€280
  - Manitas/handyman: €50–€180
  - Pintor: €300–€3,000
- **Localized stat to use:** "Los fontaneros pierden 1 de cada 4 llamadas. El 80% no deja mensaje, llaman al siguiente."
- **Per-booked-job price:** €35–€50 EUR
- **Cultural tone:** Warmer than Northern Europe, relationship-led. Slightly less formal than France.

### 4.10 Italy (IT)

- **Voice/accent:** Italian (standard, no strong regional)
- **Currency:** EUR (€)
- **Terminology:** "idraulico", "elettricista", "pronto intervento", "preventivo"
- **Common CRMs:** Praxedo, FattureInCloud, Danea
- **Compliance hook:** GDPR + Codice Privacy, EU data residency
- **Job value ranges:**
  - Idraulico: €60–€250 standard, €200–€700 pronto intervento
  - Elettricista: €80–€300 standard, €250–€650 pronto intervento
  - Termoidraulico/HVAC: €100–€400 standard, €300–€900 pronto intervento
  - Coperture (roofer): €200–€700 inspection, €2,500–€18,000 repair
  - Giardinaggio: €60–€280/job
  - Pulizie: €80–€400 commercial, €60–€180 residential
  - Disinfestazione: €100–€280
  - Tuttofare: €50–€180
  - Imbianchino: €300–€3,000
- **Localized stat to use:** "Gli idraulici perdono 1 chiamata su 4. L'80% non lascia un messaggio, chiama il successivo."
- **Per-booked-job price:** €35–€50 EUR
- **Cultural tone:** Warm, relationship-led. Personal touch matters.

### 4.11 Netherlands (NL)

- **Voice/accent:** Dutch (Randstad neutral) — or English (most Dutch tradesmen are bilingual)
- **Currency:** EUR (€)
- **Terminology:** "loodgieter", "elektricien", "spoed" (emergency), "offerte"
- **Common CRMs:** Snelstart, Exact, Tradify
- **Compliance hook:** GDPR + UAVG, EU data residency
- **Job value ranges:**
  - Loodgieter: €80–€280 standard, €250–€700 spoed
  - Elektricien: €90–€300 standard, €250–€650 spoed
  - CV/HVAC: €120–€450 standard, €350–€900 spoed
  - Dakdekker: €200–€700 inspection, €2,500–€18,000 repair
  - Hovenier: €70–€300/job
  - Schoonmaak: €100–€400 commercial, €70–€200 residential
  - Ongediertebestrijding: €100–€280
  - Klusjesman: €60–€200
  - Schilder: €350–€3,000
- **Localized stat to use:** "Loodgieters missen gemiddeld 1 op de 4 oproepen. 80% laat geen voicemail achter."
- **Per-booked-job price:** €40–€55 EUR
- **Cultural tone:** Direct, blunt, value efficiency. Dutch prospects respect "no fluff" pitches more than any other European market. Cut to the offer fast.

### 4.12 Ireland (IE)

- **Voice/accent:** Irish English
- **Currency:** EUR (€)
- **Terminology:** "tradesman", "callout", "RGI registered" (gas)
- **Common CRMs:** Joblogic, simPRO, Commusoft (UK overlap)
- **Compliance hook:** GDPR + Data Protection Act 2018 (Ireland), EU data residency
- **Job value ranges:**
  - Plumber: €80–€280 standard, €250–€700 emergency
  - Electrician: €90–€300 standard, €250–€650 emergency
  - HVAC/heating: €120–€450 standard, €350–€900 emergency
  - Roofer: €200–€700 inspection, €2,500–€15,000 repair
  - Landscaper: €70–€300/job
  - Cleaning: €100–€400 commercial, €70–€200 residential
  - Pest control: €100–€280
  - Handyman: €60–€200
  - Painter/decorator: €350–€3,000
- **Localized stat to use:** Same as UK (markets overlap), reference "Irish tradesmen"
- **Per-booked-job price:** €35–€50 EUR
- **Cultural tone:** Warm, conversational, story-led. Irish prospects respond to "the craic" — slightly more personable than UK.

### 4.13 Sweden (SE)

- **Voice/accent:** Swedish (Stockholm neutral) — or English (Swedes are highly bilingual)
- **Currency:** SEK (kr)
- **Terminology:** "rörmokare", "elektriker", "akut", "offert"
- **Common CRMs:** Visma, Fortnox, Next Technology
- **Compliance hook:** GDPR + Dataskyddsförordningen, EU data residency
- **Job value ranges:**
  - Rörmokare: 800–2,800 kr standard, 2,500–7,000 kr akut
  - Elektriker: 900–3,000 kr standard, 2,500–6,500 kr akut
  - VVS: 1,200–4,500 kr standard, 3,500–9,000 kr akut
  - Takläggare (roofer): 2,000–7,000 kr inspection, 25,000–180,000 kr repair
  - Trädgårdsmästare: 700–3,000 kr/job
  - Städ: 1,000–4,000 kr commercial, 700–2,000 kr residential
  - Skadedjursbekämpning: 1,000–2,800 kr
  - Hantverkare: 600–2,000 kr
  - Målare: 3,500–30,000 kr
- **Localized stat to use:** "Hantverkare missar i genomsnitt 1 av 4 samtal. 80% lämnar inget meddelande."
- **Per-booked-job price:** 400–550 SEK
- **Cultural tone:** Highly direct, low hype, egalitarian. Lagom — not too much, not too little. Don't oversell.

---

## 5. Business Type Personalization (Cuts Across Countries)

| Business Type | Specific Pain Hook | Emergency Type | Common Tools |
|---------------|-------------------|----------------|--------------|
| Plumber | Burst pipes, blocked drains, no hot water | After-hours emergencies | ServiceM8, Jobber |
| Electrician | Power outages, sparks, fuse box issues | Safety emergencies | Jobber, simPRO |
| HVAC | No heat in winter, no AC in summer | Seasonal surge | ServiceTitan, Jobber |
| Roofing | Leaks during rain, storm damage | Weather-driven spikes | JobNimbus, AccuLynx |
| Landscaping | Seasonal bookings, weather-dependent | Storm cleanup | Jobber, LMN |
| Cleaning | One-off bookings, recurring contracts | Last-minute requests | Jobber, ZenMaid |
| Pest control | Infestation urgency | Same-day callouts | PestPac, FieldRoutes |
| Handyman | Wide service variety | Quick-turnaround jobs | Jobber, Housecall Pro |
| Painting | Project-based, quote-heavy | Pre-event/move-in deadlines | Jobber, JobTread |
| Flooring | Quote-heavy, install scheduling | Insurance/water damage | JobTread, Jobber |

**Adapt the email opener to the business type:**
- Plumber: "When you're under a sink at 2pm..."
- Electrician: "When you're up a ladder rewiring a panel..."
- HVAC: "When you're on a roof installing a condenser..."
- Roofer: "When you're on a roof in the rain..."
- Landscaper: "When you're elbow-deep in a hedge..."
- Cleaner: "When you're mid-job at a client site..."
- Pest control: "When you're treating a property..."
- Handyman: "When you're mid-repair at a customer's place..."
- Painter: "When you're on a ladder with a roller in your hand..."
- Flooring installer: "When you're laying down planks at a job site..."

---

## 6. Email Templates (Country-Variant Examples)

### 6.1 Australia / Plumber (Direct/Outcome-led)

```
Subject: How many calls did you miss today?

Hi {{first_name}},

Quick question — when you're under a sink at 2pm, who's answering your phone?

If the answer is "voicemail" or "the missus," you're losing roughly 1 in 4 calls. 80% of those people don't leave a message. They ring the next plumber on Google.

We built an AI voice agent for Australian tradies. Australian accent, takes calls 24/7, knows the difference between a burst pipe and a slow drip, books straight into ServiceM8. Misses get an instant SMS within 15 seconds so the lead doesn't go cold.

The offer:

$0 setup. $0 monthly. You only pay when it books a job.

If it doesn't book jobs, you owe nothing. One after-hours callout pays for 10+ booked jobs through us.

10-minute demo this week?

Thanks,
Khush
OltaFlock AI
```

### 6.2 US / Plumber (Direct/Outcome-led)

```
Subject: $0 until we book your first job

Hi {{first_name}},

Quick question — when you're under a sink at 2pm, who's answering your phone?

If the answer is "voicemail" or "my office manager who's already overwhelmed," you're losing 27% of inbound calls. 80% of those callers don't leave a voicemail. They dial the next plumber.

Speed-to-lead decides who wins the job. The first contractor to respond gets the job 78% of the time.

We built an AI voice agent for US plumbers. Answers calls 24/7, triages by service type and urgency, books directly into Jobber or Housecall Pro. Missed calls get an SMS within 15 seconds.

The offer:

$0 setup. $0 monthly. Pay only when a job books.

One emergency callout covers 10+ jobs through us.

10-minute demo this week?

Thanks,
Khush
OltaFlock AI
```

### 6.3 UK / Plumber (Direct/Outcome-led)

```
Subject: You're missing 1 in 4 calls — here's the fix

Hi {{first_name}},

Quick one — when you're under a sink at 2pm, who's answering your phone?

If it's voicemail or a partner juggling other work, you're missing roughly a quarter of inbound calls. Most of those callers won't leave a message. They ring the next plumber on the list.

We built an AI voice agent for UK plumbers. British accent, takes calls 24/7, asks the right questions, books straight into Commusoft or Joblogic. Missed calls trigger an SMS within 15 seconds.

The offer:

£0 setup. £0 monthly. You only pay per booked job.

If it doesn't book jobs, you owe nothing. One emergency callout covers 10+ booked jobs through us.

GDPR-compliant, UK data residency.

10-minute demo this week?

Thanks,
Khush
OltaFlock AI
```

### 6.4 India / Plumber (Direct, WhatsApp-led)

```
Subject: Aap kitne calls miss kar rahe hain?

Hi {{first_name}},

Sawaal — jab aap kisi job pe hain, customer ka call kaun uthata hai?

Agar jawab hai "voicemail" ya "WhatsApp pe baad mein dekh lenge", aap roz 5–10 jobs kho rahe hain. 80% customers message nahi chodte. Wo agle service provider ko call karte hain.

Humne ek AI voice agent banaya hai jo:
- Hindi/English mein 24/7 calls uthata hai
- Job type aur urgency samjhta hai
- WhatsApp pe instant message bhejta hai agar call miss ho
- Aapke calendar pe direct booking karta hai

Offer:

₹0 setup. ₹0 monthly. Sirf booked job ke liye pay karein.

Agar job book nahi hota, koi charge nahi.

10 minute ka demo is week?

Thanks,
Khush
OltaFlock AI
```

---

## 7. AI Generation Instructions (For the Personalization Engine)

When generating an email for a prospect, follow this order:

1. **Read prospect data:** name, business name, business type, country, city
2. **Load country section** from this file (e.g., "AU" → Section 4.1)
3. **Load business type row** from Section 5
4. **Pick subject line pattern** based on tone needed (default: outcome-led)
5. **Fill template** with country-specific:
   - Currency symbol and amounts
   - Voice accent description
   - Common CRM (pick #1 for that country)
   - Job value ranges
   - Localized stat
   - Cultural tone phrases
6. **Adapt opener** to business type (Section 5 lookup)
7. **Validate output:**
   - Word count under 220 (warm) or 150 (cold)
   - No em dashes
   - No buzzwords
   - Currency and CRM match country
   - Opener matches business type
8. **Generate two variants:** one direct (default), one warmer (data-backed)

**Hard checks before send:**
- Does the subject line pass the "would I open this?" test?
- Is the offer crystal clear in under 10 seconds of reading?
- Does the country-specific tone match (Aussie blunt, German formal, Italian warm)?
- Is there exactly one CTA?
- Is the sign-off "Khush, OltaFlock AI"?

---

## 8. Things This File Does NOT Cover (Build Separate Configs)

- Follow-up sequences (build a separate `followup-sequences.md`)
- LinkedIn outreach (different channel, different rules)
- Cold call scripts (voice channel, different)
- Other niches (each gets its own file: `restaurants-offer.md`, `law-firms-offer.md`, etc.)
- Demo call script (the email books the demo — what happens on the demo is a separate playbook)

---

*Maintained by Khush, OltaFlock AI | Single source of truth for home services cold email*
