import { useState, type ReactNode } from "react";
import { setAutonomyLevel } from "../api/autonomy";
import { MOCK } from "../api/mock";
import {
  Button, Card, CardFooter, CardHeader, CedarBlock, Chip, Cond, DiffList, Drawer, eur, HashRow, Icon, ICON_NAMES,
  KindTag, Mark, Modal, Paper, Segmented, SignedTag, Tier, Toggle, Typing, useToast, Weight, YesNo, type Kind,
} from "../ui";
import { useComposer } from "./composer";
import { usePhoneCamera } from "./PhoneCamera";
import { useAutonomy } from "./queries";
import { useSignedChange } from "./signing";

const KINDS: Kind[] = ["must", "prefer", "usual", "about", "auto"];

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="grid gap-3 rounded-[20px] border border-line p-5">
      <h2 className="text-lg font-bold">{title}</h2>
      {children}
    </section>
  );
}

/** Dev-only page (#ui): every shared primitive, the theme switch and a live signing round trip. */
export default function UiGallery() {
  const toast = useToast();
  const sign = useSignedChange();
  const camera = usePhoneCamera();
  const autonomy = useAutonomy();
  const [on, setOn] = useState(true);
  const [seg, setSeg] = useState<"exclude" | "ask">("exclude");
  const [w, setW] = useState(2);
  const [yn, setYn] = useState<boolean | undefined>(undefined);
  const [modal, setModal] = useState(false);
  const [drawer, setDrawer] = useState(false);
  const [focus, setFocus] = useState(false);
  useComposer({ onSubmit: (t) => toast(`Composer: ${t}`), hints: ["120 L milk by Monday", "the usual, but double the oat drink"], placeholder: "Try the composer…" });

  const setTheme = (t: "light" | "dark" | "auto") => {
    if (t === "auto") delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = t;
  };
  const level = autonomy.data?.level ?? 0;

  return (
    <main className="mx-auto grid max-w-[1100px] gap-5 px-4 pt-8 pb-48">
      <div className="flex flex-wrap items-end gap-3">
        <div>
          <div className="eyebrow">Dev only</div>
          <h1 className="text-4xl font-extrabold">UI primitives</h1>
          <p className="text-ink3">Import from <Cond>src/ui</Cond>. Mock mode: <b>{MOCK ? "on" : "off"}</b>.</p>
        </div>
        <span className="flex-1" />
        <Segmented label="Theme" value="auto" options={[{ value: "light", label: "Light" }, { value: "dark", label: "Dark" }, { value: "auto", label: "System" }]} onChange={setTheme} />
      </div>

      <Section title="Signing round trip (real backend)">
        <p className="text-sm text-ink2">
          Current level: <b>{autonomy.isLoading ? "…" : autonomy.isError ? "backend not reachable" : `${level} · ${autonomy.data?.name}`}</b>. The button
          asks the backend to change it, which answers 409 with a preview; the modal shows diff, Cedar and hash; signing creates a new mandate version.
        </p>
        <div className="flex flex-wrap gap-2">
          {[0, 1, 2].map((n) => (
            <Button
              key={n}
              small
              variant={n === level ? "primary" : "ghost"}
              onClick={async () => {
                try {
                  const out = await sign({ title: `Autonomy level ${n}`, call: (mandate_hash) => setAutonomyLevel({ level: n as 0 | 1 | 2, mandate_hash }) });
                  if (out === null) toast("Cancelled, nothing changed.");
                } catch (e) {
                  toast((e as Error).message);
                }
              }}
            >
              Level {n}
            </Button>
          ))}
        </div>
      </Section>

      <Section title="Buttons">
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="primary" icon="lock">Sign & send to Valio Aimo</Button>
          <Button>Edit</Button>
          <Button variant="primary" small>Small primary</Button>
          <Button small icon="phone">Use your phone</Button>
          <Button variant="link">Open kitchen profile</Button>
          <Button variant="primary" disabled>Disabled</Button>
          <span className="rounded-2xl bg-ink px-3 py-2"><Button variant="inverse" small>Inverse (on dark)</Button></span>
        </div>
      </Section>

      <Section title="Kinds, chips, tiers">
        <div className="flex flex-wrap gap-2">{KINDS.map((k) => <KindTag key={k} kind={k} />)}<SignedTag /></div>
        <div className="flex flex-wrap gap-2">
          <Chip kind="must" icon="lock">No nuts incl. may contain</Chip>
          <Chip kind="prefer" icon="check">Finnish</Chip>
          <Chip kind="usual" icon="usual">Your usual · 14 invoices</Chip>
          <Chip kind="about">New for you</Chip>
          <Chip kind="auto" icon="lock">Alone · g1</Chip>
          <Chip kind="must" icon="flag">Label changed 9 Nov</Chip>
          <Chip>plain</Chip>
          <Chip kind="accent">accent</Chip>
          <Cond>allergens not_contains peanuts, tree_nuts</Cond>
        </div>
        <div className="flex flex-wrap gap-2">
          <Tier tier="auto">Ordered alone</Tier><Tier tier="ask">You decided 1</Tier><Tier tier="block">1 line blocked (nuts)</Tier><Tier tier="live">In progress</Tier>
        </div>
      </Section>

      <Section title="Controls">
        <div className="flex flex-wrap items-center gap-5">
          <Toggle checked={on} onChange={setOn} label="Rule on" />
          <Segmented label="If unknown" value={seg} options={[{ value: "exclude", label: "exclude" }, { value: "ask", label: "ask me" }]} onChange={setSeg} />
          <Weight value={w} onChange={setW} />
          <YesNo value={yn} onChange={setYn} />
          <Typing />
        </div>
      </Section>

      <Section title="Cards">
        <div className="grid gap-4 md:grid-cols-2">
          <Card>
            <CardHeader title="Here's what I understood"><span className="text-[13px] text-ink3">from your message</span></CardHeader>
            <div className="grid grid-cols-[1fr_auto] gap-1 px-5 py-3.5">
              <div><div className="text-xs font-semibold tracking-wide text-ink3 uppercase">milk 120 L</div><div className="font-semibold">Valio Eila® semi-skimmed 10 L Novobox</div></div>
              <div className="num font-bold">{eur(130.8)}</div>
            </div>
            <CardFooter><span className="flex-1 text-sm text-ink2"><b className="text-ink">Everything passes your rules.</b> I'll order it alone.</span><Button variant="primary" small icon="lock">Sign</Button></CardFooter>
          </Card>
          <Card variant="decision"><CardHeader title="A decision for you" /><p className="p-5 text-sm">Your usual granola now says "may contain tree nuts".</p></Card>
          <Card variant="block"><CardHeader title={<span className="flex items-center gap-2"><Icon name="ban" />Blocked by Valio Aimo</span>} /><p className="p-5 text-sm">No autonomy level can turn a block into an order.</p></Card>
          <Card variant="receipt"><CardHeader title="Valio Aimo confirmed"><span className="text-[13px] text-ink3">order #A-4471</span></CardHeader><p className="p-5 text-sm">Total {eur(576.15)}</p></Card>
        </div>
      </Section>

      <Section title="Trust">
        <HashRow label="Intent you signed" value="sha256:3fa9e1c07b52d84e19a6f0c3b7d2e5a8146c9f0b2e7d3a5c8b1f4e6a09d2c7e1" />
        <DiffList lines={[{ text: "Add must: No sesame, not even traces", kind: "add" }, { text: "Turn off: Lower CO₂ per kg", kind: "del" }, { text: "Limit: up to €800 an order (was €600)", kind: "chg" }]} />
        <CedarBlock text={'// MUST no peanuts or tree nuts incl. \'may contain\'\n@id("m1")\nforbid (principal == Buyer::"school-kitchen-espoo", action, resource)\nwhen { context.verdicts has m1 && context.verdicts.m1 != "safe" };'} />
      </Section>

      <Section title="Paper and highlights">
        <div className="grid max-w-[720px] grid-cols-1 gap-5 sm:grid-cols-3" onMouseEnter={() => setFocus(true)} onMouseLeave={() => setFocus(false)}>
          <div className="aspect-[4/5]"><Paper tilt="l"><h4>LUNCH · WEEK 47</h4>Every day: milk, oat drink, rye bread<br /><Mark kind="about">420 lunches / day</Mark> · <Mark kind="must" focus={focus}>Nut-free school</Mark></Paper></div>
          <div className="aspect-[4/5]"><Paper variant="hand" tilt="r"><b>KITCHEN – READ!</b><br /><Mark kind="must">NO NUTS – not even "may contain"!!</Mark><br /><Mark kind="usual">18 milk-free diets → oat drink</Mark></Paper></div>
          <div className="aspect-[4/5]"><Paper variant="inv"><h4>Valio Aimo · Invoice</h4>Valio Eila® 10 l novobox · 48 ×<br /><Mark kind="auto">avg. € 540 per delivery</Mark><br /><Mark kind="prefer" on={false}>not highlighted</Mark></Paper></div>
        </div>
      </Section>

      <Section title="Overlays">
        <div className="flex flex-wrap gap-2">
          <Button small onClick={() => setModal(true)}>Modal</Button>
          <Button small onClick={() => setDrawer(true)}>Drawer</Button>
          <Button small onClick={() => toast("Kitchen profile v8")}>Toast</Button>
          <Button small icon="cam" onClick={() => camera.open("kitchen")}>Camera (file picker until F3)</Button>
        </div>
        <Modal open={modal} onClose={() => setModal(false)} label="Example">
          <div className="eyebrow">Level 2 · Small deviations</div>
          <h2>We recommend waiting a little.</h2>
          <p className="text-ink3">6 of 10 orders have gone through without a correction so far.</p>
          <div className="flex justify-end gap-2"><Button onClick={() => setModal(false)}>Keep level 1</Button><Button variant="primary" onClick={() => setModal(false)}>Unlock anyway</Button></div>
        </Modal>
        <Drawer open={drawer} onClose={() => setDrawer(false)} title="Verify the receipt" icon={<span className="grid size-[26px] place-items-center rounded-full bg-prefer text-white"><Icon name="shield" size={15} /></span>}>
          <p className="text-ink3">Anyone can check this without trusting me.</p>
        </Drawer>
      </Section>

      <Section title="Icons">
        <div className="flex flex-wrap gap-3 text-ink2">{ICON_NAMES.map((n) => <span key={n} title={n} className="grid size-9 place-items-center rounded-lg bg-surface"><Icon name={n} /></span>)}</div>
      </Section>
    </main>
  );
}
