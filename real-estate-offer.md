# Real Estate — Cold Email Offer Config

> Niche: Real Estate (real estate agents, realtors, real estate brokers, property management companies, property dealers)
> Channel: Cold email
> Goal: Book a 10-minute demo call
> Last updated: 2026-05

---

## 1. Core Offer (Same Across All Countries)

**Product:** AI Lead Concierge for real estate — 24/7 voice + SMS + email agent that catches every inbound lead, revives the dead leads sitting in the CRM, and runs a 12-month auto-nurture so no lead ever goes uncontacted.

**What it does:**
- Answers every inbound call 24/7 with a local-accent voice
- Qualifies on the LPMAMA framework (location, price, motivation, agent, mortgage, appointment) in one call
- Books showings and listing appointments straight into the agent's calendar/CRM
- If the lead hangs up before pickup, instant SMS within 15 seconds (Zillow leads die in 30 minutes, this kills that)
- Revives every uncontacted lead in the CRM from the last 12 months via SMS + email + voicemail drops
- Runs a 12-month auto-nurture on every new lead so the average 1.4-touch agent finally hits the 7-12 touches a deal actually takes
- Sends showing reminders 24h and 1h ahead so no-show rate drops to near zero
- Logs every conversation, transcript, and qualifying answer back to the CRM

**Pricing offer (the hook that makes saying no feel stupid):**
- $0 setup, $0 monthly, $0 contract
- Pay only when the agent books a qualified showing OR revives a dormant lead into a real appointment
- 30-day "no shows, $200 back" guarantee: if we don't book at least 3 qualified showings or revive 5 dormant leads in the first 30 days, we refund $200 of our own money on top of you owing nothing
- One closed transaction typically pays for 50 to 80 booked showings through us

**Risk-reversal phrasing to use verbatim or paraphrase:**
- "You don't pay a cent until we book your next showing."
- "If we don't book a single showing in 30 days, I send you $200 of my own money. You still owe nothing."
- "Zero setup, zero monthly, zero contract. Aligned incentives or no deal."
- "The only way you lose money is if it works and you stop us."

**Why this beats every competitor in market:**
kvCORE, BoldTrail, Follow Up Boss, Chime, Sierra Interactive, Ylopo all charge $300 to $1,500 a month flat regardless of whether you ever book a showing. ISAs cost $40k+ a year and still cap out at 2,000 contacts. Our offer flips the risk entirely: aligned incentives, zero downside, $200 back if we underdeliver.

---

## 2. Email Framework (Same Skeleton — Variables Swap by Country)

**Subject line patterns** (pick one based on tone needed):
- Outcome-led: `How many of yesterday's Zillow leads did you actually call?`
- Loss-framed: `1.4 follow-ups before most agents quit. You?`
- Direct/blunt: `${{currency_symbol}}0 until we book your next showing`
- Curiosity: `Built something for {{business_name}} — worth 10 mins?`
- Stat-led: `78% of buyers pick the first agent who calls back`

**Body structure (5 blocks, in order):**

1. **Pattern interrupt opener** — name the prospect's specific reality (mid-showing, drafting an offer, stuck in transaction coordination)
2. **Cost of the problem** — localized stat about response-time failure or dead-database revenue leak
3. **Solution in one sentence** — voice agent + dead-database revival + 12-month nurture, country-localized voice
4. **The offer** — pay-per-booked-showing, zero risk, $200 back guarantee
5. **CTA** — 10-minute demo, low friction

**Hard rules:**
- Max 150 words for cold version, max 220 for warm/data-backed version
- Never use buzzwords ("revolutionary", "cutting-edge", "leverage", "transform")
- Never use em dashes (use commas, periods, or parentheses instead)
- Never imply experiences Khush hasn't had
- Always sign off as "Khush, OltaFlock AI"
- Use the prospect's local terminology (see country sections below)
- Never invent specific GCI numbers, Zillow spend, or transaction counts for this prospect
- Stats only come from the country playbook below

---

## 3. Personalization Variables

These get filled from prospect data before sending:

| Variable | Source | Example |
|----------|--------|---------|
| `{{first_name}}` | Prospect record | "Sarah" |
| `{{business_name}}` | Prospect record | "The Sarah Chen Group" |
| `{{city}}` or `{{suburb}}` | Prospect record | "Camp Hill" / "Brisbane" |
| `{{business_type}}` | Niche selector | "agent", "broker", "property manager", "property dealer" |
| `{{country}}` | Country selector | "US", "UK", "AU", "IN", etc. |
| `{{trade_term}}` | Country mapping | "agent" (US), "estate agent" (UK), "REA" (AU) |
| `{{currency_symbol}}` | Country mapping | "$", "£", "€", "₹" |
| `{{avg_gci}}` | Country mapping | "$9,000–$12,000" (US) |
| `{{cost_per_missed_lead}}` | Country mapping | "$427" (US) |
| `{{voice_accent}}` | Country mapping | "American", "British", "Australian", "Indian English/Hindi" |
| `{{common_crm}}` | Country + business type mapping | "Follow Up Boss", "Reapit", "Vault RE" |
| `{{lead_source_pain}}` | Country mapping | "Zillow Premier Agent", "Rightmove", "realestate.com.au" |
| `{{specific_pain}}` | Business type mapping | "showing no-shows", "dead Zillow leads", "tenant maintenance after 6pm" |
| `{{secondary_pain}}` | Business type mapping | "tire-kickers", "fake form fills", "WhatsApp pile-up" |

---

## 4. Country-Specific Personalization

### 4.1 United States (US)

- **Voice/accent:** American (regional neutral, no strong Southern/NY)
- **Currency:** USD ($)
- **Terminology:** "agent", "Realtor", "showing", "listing appointment", "prequal", "GCI", "ISA", "MLS"
- **Common CRMs:** Follow Up Boss (dominant for solo/teams), BoldTrail (formerly kvCORE), Chime, Sierra Interactive, LionDesk, Top Producer, Wise Agent
- **Common lead sources to name:** Zillow Premier Agent, Zillow Flex, Realtor.com Connections, OpCity, Ylopo, REDX (expired/FSBO), Vulcan7
- **Compliance hook:** TCPA-compliant SMS (one-tap opt-in), Do Not Call list scrubbing, state-by-state license display in voicemail
- **Numbers to use (verified from research):**
  - $427 lost per missed lead (industry baseline)
  - 78% of buyers work with the first agent who responds
  - 5-min response = 21x more conversion than 30-min
  - 62% of inquiries arrive outside business hours
  - Average agent: 15 hours to first response, 1.4 follow-ups before quitting
  - 80% of sales need 5+ follow-ups; 44% of agents stop after 1
  - 74% of leads that close do so 6+ months after initial inquiry
  - Average GCI per closing: $9,000–$12,000
  - Top recoverable revenue from a dead 2,000-contact CRM: ~$810,000 GCI annually
- **Per-booked-showing price:** $75–$125 USD (qualified, LPMAMA-passed only)
- **Per dormant-lead-revived price:** $40–$60 USD (lead replies + books any meeting, even discovery)
- **Cultural tone:** Outcome-led, ROI-focused, GCI-and-numbers driven. Americans respond to "first responder wins" framing.
- **Phrases to use:** "speed to lead", "GCI", "showing", "first responder", "dead database", "LPMAMA"
- **Phrases to avoid:** "leverage", "transform", "10x", "synergy"
- **Pennsylvania note:** PA agents fall under a separate Estora-branded campaign that pitches both Estora modules (voice agent + contract & disclosure intelligence). Do not run this generic offer against PA agents. Filter them out at scrape time or mark them for the Estora playbook.

### 4.2 United Kingdom (UK)

- **Voice/accent:** British (neutral RP or mild regional)
- **Currency:** GBP (£)
- **Terminology:** "estate agent", "viewing" (not "showing"), "valuation" (not "listing appointment"), "vendor" (seller), "purchaser" (buyer), "completion", "exchange"
- **Common CRMs:** Reapit (dominant), Alto, Vebra, Jupix, Street.co.uk, Acaboom (valuations)
- **Common lead sources to name:** Rightmove, Zoopla, OnTheMarket, Boomin (defunct, do not name)
- **Compliance hook:** UK GDPR + Data Protection Act 2018, ICO-registered, AML compliance ready (HMRC anti-money-laundering for estate agents)
- **Numbers to use:**
  - £350 average lost commission per missed lead (UK averages, lower than US due to lower fee structure)
  - Same 5-minute response window applies (Rightmove leads die just as fast)
  - 62% of viewing requests arrive outside 9-to-5
  - Average UK estate agent fee: 1.0–1.8% of sale price; on a £300k home that is £3,000–£5,400 per closing
- **Per-booked-viewing price:** £60–£100 GBP
- **Per dormant-lead-revived price:** £35–£50 GBP
- **Cultural tone:** Understated, dry humor works, hate hype. Brits respond to "sensible" and "practical" framing. Lead with arithmetic, not adjectives.
- **Phrases to use:** "viewing", "vendor", "valuation booked", "sensible", "fair pricing", "GDPR-compliant"
- **Phrases to avoid:** "crushing it", "10x", "showing" (Americanism), "Realtor" (US-only term)

### 4.3 Australia (AU)

- **Voice/accent:** Australian (non-negotiable — US accents get hung up on)
- **Currency:** AUD ($)
- **Terminology:** "REA" (real estate agent), "open home", "appraisal" (not "valuation" or "listing appointment"), "vendor", "auction", "BD" (business development)
- **Common CRMs:** Vault RE, Box+Dice, AgentBox, ReNet, MRI Software, RPM (Property Me for PM), PropertyTree
- **Common lead sources to name:** realestate.com.au (REA Group), Domain, allhomes (ACT-specific)
- **Compliance hook:** Australian Privacy Act 1988, Australian data residency, REIV/REINSW registration
- **Numbers to use:**
  - 27% of inbound calls missed on average for solo REAs, higher in regional markets
  - 80% of callers don't leave a voicemail, they ring the next agent on REA
  - Avg gross commission per sale: 1.6–2.5% of sale price; on a $750k home that is $12,000–$18,750 AUD
  - Auction campaigns generate concentrated lead bursts that solo agents physically cannot return-call inside the speed window
- **Per-booked-inspection price:** $80–$150 AUD (open home or private inspection)
- **Per dormant-lead-revived price:** $40–$60 AUD
- **Cultural tone:** Direct, no-bullshit, dry. Aussie agents hate being sold to. Lead with math, skip the hype. Auction-market agents respect speed talk because Saturday mornings are a literal logistics war.
- **Phrases to use:** "open home", "appraisal", "vendor", "auction campaign", "straight up", "no worries"
- **Phrases to avoid:** "showing" (US), "listing appointment" (US), "synergy", any hype language

### 4.4 New Zealand (NZ)

- **Voice/accent:** New Zealand English (NZ accent — Kiwis can hear the Aussie accent immediately)
- **Currency:** NZD ($)
- **Terminology:** "REA", "open home", "appraisal", "vendor", "Sale and Purchase Agreement" (SPA)
- **Common CRMs:** Vault RE (also dominant in NZ), Tall Poppy systems, SmrtAgent
- **Common lead sources to name:** Trade Me Property, realestate.co.nz, OneRoof
- **Compliance hook:** NZ Privacy Act 2020, Real Estate Authority (REA) compliance for agents
- **Numbers to use:** Same as AU (markets are similar), reference "Kiwi agents"
- **Per-booked-inspection price:** $70–$130 NZD
- **Per dormant-lead-revived price:** $35–$55 NZD
- **Cultural tone:** Even more low-key than Aussie. Modesty wins. Don't oversell.
- **Phrases to use:** "sweet as", "no worries", "give it a go", "open home"
- **Phrases to avoid:** US hype language, anything that sounds boastful, "showing"

### 4.5 Canada (CA)

- **Voice/accent:** Canadian English (close to neutral US)
- **Currency:** CAD ($)
- **Terminology:** "Realtor" (CREA-trademarked), "showing", "listing presentation", "MLS" (CREA-operated)
- **Common CRMs:** Follow Up Boss (cross-border), BoldTrail, Chime, Lone Wolf (back-office)
- **Common lead sources to name:** Realtor.ca, Zolo, Zillow (limited Canadian inventory), HouseSigma
- **Compliance hook:** PIPEDA, CASL (Canadian Anti-Spam Law — explicit consent required for SMS/email), provincial real estate council compliance
- **Numbers to use:** Same as US (data overlaps), reference "Canadian Realtors"
- **Per-booked-showing price:** $80–$130 CAD
- **Per dormant-lead-revived price:** $40–$60 CAD
- **Cultural tone:** Polite, less aggressive than US. Lead with practicality, not hype. CASL means consent language matters more than US.
- **Phrases to use:** "fair", "reliable", "CASL-compliant", "Realtor"
- **Phrases to avoid:** Hard-sell US-style language, "agent" (use "Realtor" — CREA members care about the trademark)

### 4.6 India (IN)

- **Voice/accent:** Indian English (Hindi/regional language support if requested)
- **Currency:** INR (₹)
- **Terminology:** "property dealer", "broker", "site visit" (not "showing"), "token amount", "registry", "RERA-registered"
- **Common CRMs:** Less standardized — most run on WhatsApp + Excel, some use Sell.do, Zoho, PropTiger backend, Sulekha
- **Common lead sources to name:** 99acres, MagicBricks, Housing.com, NoBroker, OLX, Sulekha, direct WhatsApp groups
- **Compliance hook:** DPDP Act 2023, RERA registration display, state-level builder/broker compliance
- **Numbers to use:** Reference WhatsApp dependency. "Most enquiries come via WhatsApp and never get a response after 8pm. The next dealer in the same locality picks them up."
- **Per-booked-site-visit price:** ₹400–₹800 INR
- **Per dormant-lead-revived price:** ₹150–₹300 INR
- **Cultural tone:** Relationship-led, more deferential opening, but direct on the offer. Investors expect quick turnaround on plot/flat enquiries.
- **Strategic note:** Volume model, not margin model. Push WhatsApp Business API integration heavily — it's the dominant lead channel. Voice agent value matters but WhatsApp recovery + nurture matters more.
- **Phrases to use:** "site visit", "RERA-registered", "WhatsApp", "broker", "investor"
- **Phrases to avoid:** "showing" (Americanism, no one uses it), "listing appointment" (no equivalent), excessive formality

### 4.7 Germany (DE)

- **Voice/accent:** German (Hochdeutsch, no strong regional)
- **Currency:** EUR (€)
- **Terminology:** "Immobilienmakler", "Besichtigung" (viewing), "Maklervertrag", "Provision" (commission)
- **Common CRMs:** onOffice (dominant), Flowfact, FIO, Propstack, Estateoffice
- **Common lead sources to name:** ImmoScout24 (dominant), Immowelt, eBay Kleinanzeigen
- **Compliance hook:** GDPR + BDSG (German federal data protection), EU data residency, Bestellerprinzip awareness
- **Numbers to use:** German agents lose more leads to slow response than they admit. Bestellerprinzip means seller pays commission on most rentals; agents need to win the listing fast.
- **Per-booked-Besichtigung price:** €60–€100 EUR
- **Per dormant-lead-revived price:** €30–€50 EUR
- **Cultural tone:** Formal Sie unless prospect indicates otherwise. Precision matters more than warmth. Lead with technical accuracy and DSGVO compliance.
- **Phrases to use:** "Besichtigung", "DSGVO-konform", "Maklervertrag"
- **Phrases to avoid:** Excessive friendliness, US-style hype, casual "du"

### 4.8 France (FR)

- **Voice/accent:** French (Île-de-France neutral)
- **Currency:** EUR (€)
- **Terminology:** "agent immobilier", "négociateur", "visite", "mandat", "compromis de vente"
- **Common CRMs:** Apimo, Hektor, Périclès, Netty
- **Common lead sources to name:** SeLoger, Leboncoin Immobilier, Bien'ici, Logic-Immo
- **Compliance hook:** RGPD + CNIL, EU data residency, carte professionnelle (T-card) compliance
- **Numbers to use:** "Les agents immobiliers manquent en moyenne 1 demande de visite sur 4. 80% des prospects ne laissent pas de message — ils contactent l'agence suivante."
- **Per-booked-visite price:** €60–€100 EUR
- **Per dormant-lead-revived price:** €30–€50 EUR
- **Cultural tone:** Formal "vous" by default. French prospects appreciate intellectual framing of the problem before the solution. Don't rush to the pitch.
- **Phrases to use:** "visite", "mandat", "RGPD-conforme", "négociateur"

### 4.9 Spain (ES)

- **Voice/accent:** Castilian Spanish (or Latin American neutral if pitching regionally)
- **Currency:** EUR (€)
- **Terminology:** "agente inmobiliario", "API" (Agente de la Propiedad Inmobiliaria), "visita", "exclusiva" (exclusive listing)
- **Common CRMs:** Inmovilla, Witei, Inmobalia, Sooprema
- **Common lead sources to name:** Idealista (dominant), Fotocasa, Pisos.com, Habitaclia
- **Compliance hook:** GDPR + LOPDGDD, EU data residency
- **Numbers to use:** "Los agentes inmobiliarios pierden 1 de cada 4 solicitudes de visita. El 80% no deja mensaje, llama al siguiente."
- **Per-booked-visita price:** €50–€90 EUR
- **Per dormant-lead-revived price:** €25–€45 EUR
- **Cultural tone:** Warmer than Northern Europe, relationship-led. Slightly less formal than France.
- **Phrases to use:** "visita", "exclusiva", "API"

### 4.10 Italy (IT)

- **Voice/accent:** Italian (standard)
- **Currency:** EUR (€)
- **Terminology:** "agente immobiliare", "visita", "mandato", "preliminare", "rogito"
- **Common CRMs:** GestionaleImmobiliare, RealGimm, ImmobilCloud, GetSolution
- **Common lead sources to name:** Immobiliare.it (dominant), Casa.it, Subito.it Immobili
- **Compliance hook:** GDPR + Codice Privacy, EU data residency
- **Numbers to use:** "Gli agenti immobiliari perdono 1 richiesta di visita su 4. L'80% non lascia un messaggio, chiama il successivo."
- **Per-booked-visita price:** €50–€90 EUR
- **Per dormant-lead-revived price:** €25–€45 EUR
- **Cultural tone:** Warm, relationship-led. Personal touch matters.

### 4.11 Netherlands (NL)

- **Voice/accent:** Dutch (Randstad neutral) — or English (most Dutch agents are bilingual)
- **Currency:** EUR (€)
- **Terminology:** "makelaar", "bezichtiging", "vraagprijs", "NVM-makelaar" (industry body)
- **Common CRMs:** Realworks, Skarabee, Kolibri24
- **Common lead sources to name:** Funda (dominant), Jaap, Pararius
- **Compliance hook:** GDPR + UAVG, EU data residency, NVM compliance
- **Numbers to use:** "Makelaars missen gemiddeld 1 op de 4 bezichtigingsverzoeken. 80% laat geen voicemail achter."
- **Per-booked-bezichtiging price:** €60–€100 EUR
- **Per dormant-lead-revived price:** €30–€50 EUR
- **Cultural tone:** Direct, blunt, value efficiency. Dutch prospects respect "no fluff" pitches more than any other European market. Cut to the offer fast.

### 4.12 Ireland (IE)

- **Voice/accent:** Irish English
- **Currency:** EUR (€)
- **Terminology:** "auctioneer" or "estate agent", "viewing", "valuation", "BER cert" (Building Energy Rating)
- **Common CRMs:** Acquaint CRM, Reapit (UK overlap), Alto
- **Common lead sources to name:** Daft.ie (dominant), MyHome.ie, Property.ie
- **Compliance hook:** GDPR + Data Protection Act 2018 (Ireland), PSRA (Property Services Regulatory Authority) compliance
- **Numbers to use:** Same as UK with reference to "Irish auctioneers" or "estate agents"
- **Per-booked-viewing price:** €60–€100 EUR
- **Per dormant-lead-revived price:** €30–€50 EUR
- **Cultural tone:** Warm, conversational, story-led. Irish prospects respond to a personal opener — slightly more personable than UK.

### 4.13 Sweden (SE)

- **Voice/accent:** Swedish (Stockholm neutral) — or English (Swedes are highly bilingual)
- **Currency:** SEK (kr)
- **Terminology:** "fastighetsmäklare", "visning", "objekt", "budgivning" (bidding war)
- **Common CRMs:** Vitec Express, Fastighetsbyrån-internal systems, ResVerk
- **Common lead sources to name:** Hemnet (dominant), Booli, Blocket Bostad
- **Compliance hook:** GDPR + Dataskyddsförordningen, EU data residency, FMI registration (Fastighetsmäklarinspektionen)
- **Numbers to use:** "Fastighetsmäklare missar i genomsnitt 1 av 4 visningsförfrågningar. 80% lämnar inget meddelande."
- **Per-booked-visning price:** 600–950 SEK
- **Per dormant-lead-revived price:** 300–500 SEK
- **Cultural tone:** Highly direct, low hype, egalitarian. Lagom — not too much, not too little. Don't oversell.

---

## 5. Business Type Personalization (Cuts Across Countries)

| Business Type | Specific Pain Hook | Lead Type | Common Tools |
|---------------|-------------------|-----------|--------------|
| Real estate agent / Realtor | Speed-to-lead failure on Zillow/Rightmove leads, dead CRM with thousands of uncontacted contacts, no time for 7-12 touches per lead | Buyer + seller inquiries, expired/FSBO outreach | Follow Up Boss, BoldTrail, Reapit, Vault RE |
| Real estate broker | Above + agents on the team have inconsistent follow-up discipline, brokerage leaks revenue across every desk | Mixed buyer/seller, recruiting agent leads | BoldTrail, Sierra Interactive, Chime, Lone Wolf |
| Property management company | After-hours maintenance calls (leak, lockout) hit voicemail, tenant inquiries scatter across phone/email/WhatsApp/portal, applicant screening is slow | Tenant maintenance + new rental applicants + owner inquiries | Buildium, AppFolio, Yardi, Property Tree (AU), MRI |
| Property dealer (India-focused) | 50+ WhatsApp inquiries pile up, investor leads on plots/flats never get followed up after 2 weeks, voicemail does not exist as a habit | Plot/flat/shop/office buyer + investor inquiries | WhatsApp + Excel mostly, sometimes Zoho, Sell.do |

**Adapt the email opener to the business type:**
- Real estate agent / Realtor: "When you're in a showing at 3pm, who's answering the inbound from your Zillow ad?"
- Real estate broker: "When your top agent is mid-listing-presentation, who's responding to the 11 leads that came in this morning?"
- Property management company: "When a tenant calls at 9pm about a leak, who's picking up?"
- Property dealer: "Aap kal kitne WhatsApp enquiries ko miss kiye, jab aap kisi site visit pe the?"

**Adapt the offer wording to the business type:**
- Agent / Realtor: "Pay only when we book a qualified showing"
- Broker: "Pay only when we book a qualified showing for any agent on your team"
- Property management: "Pay only when we book a qualified rental tour OR resolve a maintenance dispatch"
- Property dealer: "Pay only when we book a confirmed site visit"

---

## 6. Email Templates (Country-Variant Examples)

### 6.1 US / Real Estate Agent (Direct/Outcome-led)

```
Subject: $0 until we book your next showing

Hi {{first_name}},

Quick question. When you're in a showing at 3pm, who's calling back the Zillow lead that came in five minutes ago?

If the answer is "voicemail" or "I'll get to it tonight," you've already lost the lead. 78% of buyers go with the first agent who responds. Average industry response is 15 hours. Your CRM probably has 2,000+ leads from the last year that got 1.4 follow-ups before everyone gave up. 74% of those will close with someone, just not with you.

We built an AI voice agent for US Realtors. American voice, takes calls 24/7, qualifies on LPMAMA, books showings straight into Follow Up Boss or BoldTrail. Misses get an SMS within 15 seconds. We also revive every uncontacted lead in your CRM and run a 12-month auto-nurture so the 7-to-12-touch problem solves itself.

The offer:

$0 setup. $0 monthly. You only pay when we book a qualified showing or revive a dormant lead into a real meeting.

If we don't book at least 3 qualified showings in 30 days, I send you $200 of my own money. You still owe nothing.

10-minute demo this week?

Thanks,
Khush
OltaFlock AI
```

### 6.2 UK / Estate Agent (Direct/Outcome-led)

```
Subject: 1 in 4 viewings, lost to voicemail

Hi {{first_name}},

Quick one. When you're at a valuation across town, who's picking up the Rightmove enquiry that just came in?

If it's voicemail or a colleague juggling three other calls, the vendor has already rung the next agent on the list. Most buyers contact one agent. Most who don't get a response in five minutes go with the agent who did. Your CRM probably has hundreds of enquiries from the last 12 months that received one or two follow-ups and then nothing.

We built an AI voice agent for UK estate agents. British voice, takes calls 24/7, qualifies the enquiry, books viewings straight into Reapit or Alto. Missed calls trigger an SMS within 15 seconds. We also revive every dormant enquiry in your CRM and run a 12-month nurture so cold leads warm up on their own time.

The offer:

£0 setup. £0 monthly. You only pay when we book a qualified viewing or revive a dormant enquiry.

If we don't book at least 3 viewings in 30 days, I send you £200. You still owe nothing.

GDPR-compliant, UK data residency.

10-minute demo this week?

Thanks,
Khush
OltaFlock AI
```

### 6.3 AU / Real Estate Agent (Direct/Outcome-led)

```
Subject: 27% of REA enquiries, gone before you ring back

Hi {{first_name}},

Straight up. When you're running a Saturday auction campaign, who's answering the realestate.com.au enquiry that came in mid-open-home?

If it's voicemail, the vendor or buyer has already rung the next REA in the suburb. 80% don't leave a message. Your CRM probably has thousands of enquiries from the last 12 months that got one or two callbacks before everyone moved on.

We built an AI voice agent for Australian REAs. Australian voice, takes calls 24/7, qualifies the enquiry, books inspections and appraisals straight into Vault RE or AgentBox. Missed calls trigger an SMS within 15 seconds. We also revive every dormant enquiry in your CRM and run a 12-month auto-nurture, so the leads you forgot about start booking inspections again.

The offer:

$0 setup. $0 monthly. You only pay when we book a qualified inspection or revive a dormant enquiry into a real meeting.

If we don't book at least 3 inspections in 30 days, I send you $200 AUD. You still owe nothing.

Australian Privacy Act compliant, Australian data residency.

10-minute demo this week?

Thanks,
Khush
OltaFlock AI
```

### 6.4 India / Property Dealer (Direct, WhatsApp-led)

```
Subject: WhatsApp pe kitne enquiries pending hain?

Hi {{first_name}},

Sawaal. Jab aap kisi site visit pe hote hain, WhatsApp pe aaye 50+ messages ko kaun reply karta hai?

Agar jawab hai "shaam ko dekhunga" ya "abhi busy hoon", aap roz 3-5 serious investors kho rahe hain. Wo agle property dealer ke pass ja chuke hote hain. Aapke phone mein pichle 12 mahine ke 1000+ enquiries hain jinka koi follow-up nahi hua.

Humne ek AI agent banaya hai jo:
- Hindi/English mein 24/7 calls aur WhatsApp messages handle karta hai
- Plot, flat, shop, office, har enquiry ko qualify karta hai (location, budget, urgency)
- Site visits aapke calendar pe directly book karta hai
- Pichle 12 mahine ke har dead enquiry ko revive karta hai
- 12-month auto follow-up sequence chalata hai (SMS, WhatsApp, email)

Offer:

₹0 setup. ₹0 monthly. ₹0 contract.

Sirf tab pay karein jab hum ek confirmed site visit book karein, ya purana lead revive karke meeting laayein.

Agar 30 din mein 5 site visits book nahi hote, ₹2000 main wapas bhejta hoon. Aap fir bhi kuch nahi dete.

10 minute ka demo is week?

Thanks,
Khush
OltaFlock AI
```

### 6.5 US / Property Management Company (Direct/Outcome-led)

```
Subject: Who picks up at 9pm when a tenant calls about a leak?

Hi {{first_name}},

Quick one. When a tenant calls at 9pm about a burst pipe or a lockout, who's answering?

If it's an after-hours service or voicemail, you're paying for a maintenance contractor to roll a truck before anyone has even triaged the call. New rental applicants who call after 6pm probably never get a callback either, so they sign with the next building on Zillow.

We built an AI voice agent for US property management companies. Triages tenant maintenance calls (urgent vs routine), dispatches to the right vendor, books rental tours from inbound applicants, and answers owner enquiries 24/7. Books straight into AppFolio or Buildium. Missed calls get an SMS within 15 seconds. We also revive every dormant applicant from the last 12 months.

The offer:

$0 setup. $0 monthly. You only pay when we book a qualified rental tour or correctly triage a maintenance dispatch.

If we don't book 3 tours or save 5 maintenance dispatches in 30 days, I send you $200. You still owe nothing.

10-minute demo this week?

Thanks,
Khush
OltaFlock AI
```

---

## 7. AI Generation Instructions (For the Personalization Engine)

When generating an email for a prospect, follow this order:

1. **Read prospect data:** name, business name, business type, country, city
2. **PA filter:** if `country == "US"` and `state == "PA"`, do NOT generate a draft from this file. Mark the lead for the separate Estora-branded playbook (which pitches both Estora modules). Skip to next lead.
3. **Load country section** from this file (e.g., "AU" → Section 4.3)
4. **Load business type row** from Section 5
5. **Pick subject line pattern** based on tone needed (default: outcome-led)
6. **Fill template** with country-specific:
   - Currency symbol and amounts
   - Voice accent description
   - Common CRM (pick #1 for that country + business type)
   - Lead source name (Zillow / Rightmove / realestate.com.au / 99acres)
   - Per-booked-showing price
   - Localized stat
   - Cultural tone phrases
7. **Adapt opener** to business type (Section 5 lookup)
8. **Validate output:**
   - Word count under 220 (warm) or 150 (cold)
   - No em dashes
   - No buzzwords (leverage, transform, synergy, 10x, revolutionary, cutting-edge)
   - Currency, CRM, and lead source match country
   - Opener matches business type
   - Sign-off is "Khush, OltaFlock AI"
9. **Generate two variants:** one direct (default), one warmer (data-backed with the 78%-of-buyers stat)

**Hard checks before send:**
- Does the subject line pass the "would I open this?" test?
- Is the offer crystal clear in under 10 seconds of reading?
- Does the country-specific tone match (US ROI-led, UK understated, Aussie blunt, German formal, Italian warm)?
- Is the prospect's actual lead-source platform named (Zillow / Rightmove / realestate.com.au / 99acres / Funda / etc.)?
- Is there exactly one CTA?
- Does it use "viewing" in UK/IE, "open home" or "inspection" in AU/NZ, "showing" in US/CA, "visite" in FR, "site visit" in IN?

---

## 8. Things This File Does NOT Cover (Build Separate Configs)

- Pennsylvania real estate agents — these go through the separate **Estora AI playbook** which pitches both modules (voice agent + contract & disclosure intelligence) under the Estora brand, not OltaFlock
- Follow-up sequences across the 7-step drip — see `sequencer.py` and Section 9 below
- LinkedIn outreach (different channel, different rules per CLAUDE.md §8)
- Cold call scripts (voice channel, different)
- Other niches (each gets its own file: `restaurants-offer.md`, `law-firms-offer.md`, `home-services-offer.md`, etc.)
- Demo call script (the email books the demo — what happens on the demo is a separate playbook)
- Commercial real estate brokers (CRE has different deal cycles, GCI structures, and tools — needs its own file)
- Mortgage brokers and loan officers (overlap, but different workflow — separate file)

---

## 9. Sequence Step Copy Principles

The 7-step drip is governed by two laws on top of everything in sections 1–8:

**Law 1: Every step solves "why now."** Step 1 sells the problem (78% of buyers go with the first responder). Step 3 sells loss aversion (your competitor down the street is already running this). Step 5 sells arithmetic (one dormant lead reactivated pays for 50 booked showings through us). Step 7 sells closure. The reader must finish each email knowing why they should reply *today*, not next quarter.

**Law 2: The offer compounds, not the pressure.** By step 5 the risk-reversal should feel inevitable, not desperate. Frame: "the only way you lose money is if it works and you stop us." That sentence ships in step 5 verbatim. Do not weaken it.

Step 6 is the only step that breaks tone. It is allowed to be self-aware and slightly funny ("either my emails are landing in spam or {{business_name}} doesn't actually want more booked showings, and I genuinely cannot tell which"). The pizza breakup in step 7 is the second tone break and the only one with a fixed mechanic (reply PIZZA / CALL / LATER). Both are deliberate pattern-interrupts after five formal touches.

**Step-specific real-estate angles to rotate across the 7 steps:**

- Step 1: Speed-to-lead failure on their dominant lead source (Zillow / Rightmove / realestate.com.au / 99acres)
- Step 2: The 1.4-follow-ups stat. "Most agents quit at touch two. Deals close at touch seven."
- Step 3: Competitor angle. "An agent two suburbs over is already running this. They are catching the after-6pm enquiries that used to ring through to your voicemail."
- Step 4: Loom value drop. Show how the agent qualifies a real Zillow lead from cold call to booked showing in under 4 minutes.
- Step 5: Grand-slam math recap. "Average GCI is {{avg_gci}}. One dormant lead we revive into a transaction pays for the next 50 to 80 booked showings through us. The only way you lose money is if it works and you stop us."
- Step 6: Self-aware pattern interrupt. Optional meme image (the agent-staring-at-phone-during-showing meme works well here).
- Step 7: Pizza breakup. "Reply PIZZA to stop, CALL to book a slot, LATER to circle back in 90 days."

---

*Maintained by Khush, OltaFlock AI | Single source of truth for real estate cold email*
