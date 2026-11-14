import { useToast } from "../ui";
import { useComposer } from "./composer";

/** Empty screen until its feature package lands. Registers a composer so the shell can be tried out. */
export function Placeholder({ pkg, title, note, composer = true }: { pkg: string; title: string; note?: string; composer?: boolean }) {
  const toast = useToast();
  useComposer(composer ? { onSubmit: (t) => toast(`${pkg} will handle: “${t}”`), hints: [] } : null);
  return (
    <main className="mx-auto max-w-[1280px] px-4 pt-12 pb-48">
      <div className="eyebrow">Package {pkg}</div>
      <h1 className="mt-2 text-4xl font-extrabold">{title}</h1>
      <p className="mt-3 text-ink3">
        Built in docs/work-packages/{pkg}-*.md{note ? ` · ${note}` : ""}.
        {import.meta.env.DEV && (
          <>
            {" "}All shared building blocks: <a className="link" href="#ui">#ui</a>.
          </>
        )}
      </p>
    </main>
  );
}
