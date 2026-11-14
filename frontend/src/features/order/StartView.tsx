import { usePhoneCamera } from "../../app/PhoneCamera";
import { SIGNER_NAME } from "../../app/queries";
import { Button, Icon, Paper } from "../../ui";
import { useKitchenContext } from "./data";

function greeting() {
  const h = new Date().getHours();
  return h < 11 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
}

function PhotoCard() {
  const camera = usePhoneCamera();
  return (
    <div className="photo-card" role="button" tabIndex={0} onClick={() => camera.open("order")} onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && camera.open("order")}>
      <span className="ic"><Icon name="cam" /></span>
      <span className="relative z-[1] grid gap-1.5">
        <h2>Take a picture of your order</h2>
        <p>A list, a note, a whiteboard. For tomorrow or next week: I read the date too.</p>
      </span>
      <span className="acts">
        <Button variant="primary" icon="phone" onClick={(e) => { e.stopPropagation(); camera.open("order"); }}>Use your phone</Button>
        <Button icon="clip" onClick={(e) => { e.stopPropagation(); camera.upload("order"); }}>Upload a file</Button>
      </span>
      <span className="stack" aria-hidden="true">
        <Paper variant="hand" tilt="l">
          <b>Order – Wed!</b><br />milk 120 L<br />butter 10 kg<br />rye rolls 300<br />apples 40 kg<br />granola 15 kg
        </Paper>
      </span>
    </div>
  );
}

/**
 * Before a chat starts: only two ways in. The big photo tile, or the chat at the bottom (type or speak).
 * Both lead into the same order thread; the usual order lives in the Kitchen tab, past orders in Activity.
 */
export function StartView() {
  const k = useKitchenContext();
  const by = k.kitchen?.history?.at(-1)?.by;
  const first = (by && !by.includes(".") ? by : SIGNER_NAME).split(/\s+/)[0];
  return (
    <section className="hello">
      <h1>{greeting()}, {first}.</h1>
      <p>What does the kitchen need?</p>
      <PhotoCard />
      <div className="or-chat" aria-hidden="true">
        <span>or tell me below</span>
        <Icon name="arrow" size={14} style={{ transform: "rotate(90deg)" }} />
      </div>
    </section>
  );
}
