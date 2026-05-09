# Echolocate — User Stories

These four perspectives drive the design. Every feature in the product
should be defensible from at least one of them; features that aren't are
candidates to cut.

---

## 1. Sarah — student in a public space, *during* a pandemic

> *I just want to know if the library is safe to enter, and I want to be
> sure the library doesn't know who I am.*

Sarah is a UCSD undergrad. She walks up to Geisel Library and sees a small
sign next to the door:

> **This space is monitored by Echolocate.**
> No images. No identities. No tracking.
> Live status & full audit: scan the QR or visit `library.ucsd.edu/echo`.

She scans. The page she lands on is **public, no login**. She sees:

- A green/yellow/red dot: **"calm — about 6 people right now"**
- "**Camera last activated 23 minutes ago.** The system briefly saw 4 people
  clustered near the entrance. The image was deleted immediately. We kept the count."
- "**Today the system has activated the camera 7 times.**" (with timestamps + counts)
- A "**Verify the device**" button that opens `http://echolocate.local/health` —
  she can see the same data the operator sees.

She decides to come back in 30 minutes.

When she does, the space is now yellow ("busy"). She taps **"Enable
notifications"** — a one-tap flow asks for push permission, registers an
anonymous token (which she can see + delete from her own phone), and sets
her up for crowding alerts.

Three days later her phone buzzes:

> *Echolocate · 9:14 AM*
> *You may have shared a space with someone who reported a positive test.
> The system does not know who they are. You don't need to do anything;
> consider monitoring for symptoms.*

Her first instinct is to ask: *what does the library know about me?*
She opens the app, taps her token, sees:

- **Visits:** 4 zone-time entries (just `library/main, 2026-05-09T11:14:23` etc.)
- **No name. No email. No phone. No device ID.**
- **A delete-everything button**, locally and server-side.

She trusts it because she can read it.

**What she needs the product to do that drives features:**
- A public transparency page that doesn't require enrollment
- AI activity log visible to the watched, not just the operator
- Token introspection + delete-everything button
- Plain-language status (no "variance ratio")

---

## 2. Marcus — regular at a coffee shop, *before* any pandemic

> *No one is sick. I'm just trying to find a quiet table to work.*

Marcus works remote three days a week from a coffee shop near campus.
There's no pandemic — Echolocate has been re-pitched as a "space comfort"
tool for businesses. Marcus is mildly suspicious of any "smart sensor" in
public space, so the bar to gain his trust is high.

He sees the small sign at the counter:

> **We use Echolocate to manage seating comfort. No images, no IDs. Tap to verify.**

He taps. The page shows:

- "**Right now: calm.** Best window seat is probably free."
- "**Today's pattern:** packed 11:30-1:00, calm 2:00-4:30. Quietest at 3:15."
- "What the device knows: nothing personal. Audit me →" (link to firmware /health)

He uses it daily. Over a week, the AI Decision Log shows him:

> *2026-05-12 — AI suggested moving the cream/sugar station 1m left to clear
>  the door queue. Operator status: **accepted**. Implemented 2026-05-14.*
>
> *2026-05-14 — AI suggested limiting laptop work to back tables during lunch.
>  Operator status: **rejected**. Notes: "We want laptop friends here."*

Marcus likes that the shop's decisions are visible *and* that the operator
sometimes overrides the AI. The system isn't running the place; the human is.

In a non-pandemic context, this is the only viable framing. The product
must justify itself as **useful infrastructure** (when's it least busy?
should we add tables?) without leaning on emergency authority.

**What he needs the product to do that drives features:**
- Pattern view (today / this week)
- Visible "AI proposed → human decided" loop, including overrides
- Zero-friction public access
- Honest "what is/isn't collected" page that stays one tap away

---

## 3. Yvonne — owner of a 14-seat bakery, the *paying* customer

> *I'm one person. I'm not running a Fortune 500 store. I need this thing
> to tell me what to do, not give me a dashboard to interpret.*

Yvonne hires her teenage nephew as IT support twice a year, and that's it.
She bought the Echolocate sensor because:

1. New post-pandemic regs require her to track and report capacity.
2. Yelp keeps dinging her for slow service at lunch and she suspects the
   counter layout is the problem.
3. CCTV would solve this — but she's morally uncomfortable with it AND
   her insurance would charge more for the data-breach liability.

The first thing she opens is the operator dashboard. **In "Plain" mode**
(a toggle in the corner), she sees:

```
Today is calmer than usual.
At 12:15 PM the bakery was crowded for 18 minutes.
The doorway was the busiest spot 3 times this week.
```

She does not see "variance ratio 1.34". She can flip a switch labeled
**"Show numbers"** if her nephew comes over.

Her **AI Decision Log** has cards she can act on:

> **The display case crowds the door at lunch.**
> *Across 8 lunch rushes, 6 had a tight cluster between the case and the
> entry. Suggestion: move the case 1m east to separate flowing and
> stationary traffic. Estimated impact: ~30% less queue spillover.*
>
> *[Considered]* *[Accepted]* *[Rejected]*  *(Add a note)*

She marks it "Considered, talk to landlord," types one sentence. Two months
later, the same suggestion has reappeared 3 times — she can see the trend
in her decision log and finally moves the case.

She generates a **Space Design Report** at the end of the month and
prints it out for her quarterly insurance review. The report is plain
prose — section headers, recommendations, "Methodology Note" explaining
exactly what data was used (and what wasn't).

Her dashboard also shows **community feedback** that's been collected
from anyone who used the public transparency page:

> *Anonymous · "The line from the door reaches the cream station — felt
>  cramped at lunch."*  (matches AI observation, +1 evidence)

She's satisfied because:
- She doesn't have to interpret graphs.
- The AI gives her *suggestions*, she makes the *decisions*, and her
  decisions are logged for her own records (not to call her out).
- Her customers see the same data she does — there's no "two faces."
- Insurance loves the methodology note.

**What she needs the product to do that drives features:**
- Plain-language mode (toggle, not buried)
- AI Decision Log with explicit accept/reject + notes
- Recurring-pattern detection ("3rd time this month")
- Report export
- Community feedback synthesized into the same dashboard

---

## 4. Patrick — the developer (me)

> *Honest review of my own work.*

**Does this solve a real problem?** Yes for narrow segments (small/mid
businesses, libraries, religious spaces, clinics' waiting rooms) where
CCTV is overkill or legally fraught and CO2 sensors miss the spatial
question. The "moral CCTV" framing is a real wedge.

**Is it viable to implement?** The hardware path is real — ESP32-S3 at
$9 + a webcam — and the firmware is now driven by ICMP ping which
reliably triggers CSI on commodity APs. The backend has 36 passing
tests. The PWA is vanilla, zero build, served from FastAPI. So: yes,
end-to-end in a hackathon timeframe.

**Mocked / fake data risk:**

- The simulator's noise levels (`LEVEL_NOISE = {"empty":(1.5,2.0), ...}`)
  are calibrated to make the unit tests classify correctly. Real CSI may
  differ. **The architecture is sound; the thresholds need recalibration
  on real hardware.** If we demo against the simulator and present the
  classification as physically validated, that's misrepresenting.
  Mitigation: the diagnostics tab shows the device's `firmware` field —
  `simulator` vs. `esp32s3` — so judges can tell at a glance.
- Stub responses for Claude (when no API key is set) produce reasonable
  text but obviously aren't real reasoning. Mitigation: every stub
  response is clearly labeled `(stub — no ANTHROPIC_API_KEY)`. We are
  not deceiving anyone.

**Blockers / latency:**

- *Blocker:* I don't have ESP32 hardware in this environment. The ICMP
  fix is correct against the spec but isn't end-to-end validated. To
  reduce risk I should land it as an unambiguous patch that compiles
  cleanly and follows the upstream `esp_ping` API.
- *Blocker:* iOS push requires HTTPS; ngrok is the demo-day plan.
  Note: ngrok URLs change between sessions — if the judge enrolls on
  one URL and the next ngrok URL is different, push will drop.
- *Latency:* CSI parse <1 ms; classification 5 s window (intentional);
  Claude Vision 1-3 s; Web Push 1-3 s. The user-visible end-to-end
  "crowd → notification" loop is about 8 s.

**Accuracy tradeoffs (this is where to be honest):**

| Where | Honest accuracy | What we *claim* | OK? |
|---|---|---|---|
| Empty vs. crowded | High (clear ratio gap) | "level: empty/low/moderate/high" | ✓ |
| Exact people count | ±50%+ | `count_estimate` is a *rough* number | ⚠ should rename or hide |
| AI cluster locations | 60-80% from literature | Returned as authoritative JSON | ⚠ need confidence + reasoning trace |
| Spatial recommendations | Untested in this codebase | Presented as actionable advice | ⚠ need operator override prominently |

The accuracy tradeoffs are *manageable* if the product is honest about
them. They become deceptive if we present a 4-people estimate as a
people-counter or a Claude-generated recommendation as a directive.
The fix isn't more accuracy — it's more transparency: every number gets
a confidence band, every AI judgment gets a reasoning trace and an
operator override.

**Bottom line as the developer:** I'd ship this as a v0 to a small bakery
or a library tomorrow if I had real hardware. I would *not* ship it as
"AI-powered crowd analytics" to a stadium operator. The product is
right-sized for spaces where the goal is *better space design*, not
real-time crowd ops.
