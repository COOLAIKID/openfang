# GrowthOS Design System

Positioning drives design: GrowthOS sells **revenue**, not AI. The visual
language is calm, enterprise, premium — Linear/Stripe/Vercel lineage. No neon,
no generic AI gradients. Motion is information, not decoration.

## Color tokens

| Token | Hex | Usage |
|---|---|---|
| `primary` | `#2563EB` | CTAs, links, active states, data series 1 |
| `secondary` | `#0F172A` | Dark sections, headings, logo block |
| `accent` | `#14B8A6` | Highlights, positive deltas, data series 2 |
| `success` | `#22C55E` | Scores ≥70, won states, checkmarks |
| `warning` | `#F59E0B` | Scores 50–69, medium severity |
| `destructive` | `#EF4444` | Critical issues, scores <50, loss states |
| `background` | `#FFFFFF` | Page background (marketing) |
| `surface` | `#F8FAFC` | App background, section alternation |
| `foreground` | `#0F172A` | Body text |
| `muted-foreground` | `#64748B` | Secondary text, labels |
| `border` | `#E2E8F0` | 1px hairlines everywhere |

Exposed as CSS variables in `app/globals.css`, mapped in
`tailwind.config.ts` (`bg-primary`, `text-muted-foreground`, …).

## Typography

- **Inter** (`--font-sans`) with `cv11/ss01` features; **JetBrains Mono**
  (`--font-mono`) for emails, code, and tabular data accents.
- Scale: hero 56–64/1.05 tracking-tight; section titles 36–40; card titles
  16–18 semibold; body 14–16; data UI 13–14 with `tabular-nums`.

## Spacing & surfaces

- Sections: `py-20`–`py-24`; container `max-w-7xl px-6`.
- Cards: white, `rounded-xl`, 1px border, `shadow-card`; hover lift to
  `shadow-elevated`. Hero/pricing highlight uses `shadow-glow`.
- Radius scale from `--radius: 0.625rem`.

## Motion (Framer Motion)

- Scroll reveals: `whileInView` fade + 12px rise, `viewport={{ once: true }}`,
  0.4–0.6s ease-out, ≤80ms stagger.
- Numbers count up on first view; agent runs animate step-by-step
  (spinner → check) to dramatize work being done.
- Tab/panel switches: `AnimatePresence` crossfade ≤200ms.
- Never animate layout on scroll-linked timelines; respect
  `prefers-reduced-motion` where feasible.

## Data-viz conventions

- Score semantics: <50 destructive, 50–69 warning, ≥70 success.
- Charts: recharts, 1px grid lines in border color, primary/accent series,
  currency-formatted tooltips, no 3D, no drop shadows.

## Component inventory

Primitives (`components/ui`): button (default/secondary/accent/outline/ghost/
link/destructive × sm–xl), card, input, textarea, label, badge (semantic
variants), dialog, tabs, progress, separator, skeleton, accordion, avatar,
select, dropdown-menu.

Product (`components/dashboard`): StatCard, ScoreRing, AgentRunner,
SeverityBadge, PageHeader, milestone checklist.

Marketing (`components/marketing`): Navbar, Hero (animated preview),
LogoCloud, Problem, Solution, ProductDemo, Features, RoiCalculator,
Testimonials, Pricing, Faq, FinalCta, StickyCta, ExitIntentModal.

## Voice

Outcome-first, specific, quantified. Every headline names a business result
("Find more customers", "Recover $38K/mo") — never a technology. CTAs are
possessive and concrete: "Get **My** Growth Plan."
