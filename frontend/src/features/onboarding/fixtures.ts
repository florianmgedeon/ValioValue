// "Skip, use example kitchen": the demo documents in contracts/fixtures/uploads/. The backend recognises them by
// file name and content and answers with their fixed extraction (also with LLM_PROVIDER=scripted).
import allergyNote from "../../../../contracts/fixtures/uploads/allergy-note.jpg?url";
import invoices from "../../../../contracts/fixtures/uploads/invoices-oct.pdf?url";
import menu from "../../../../contracts/fixtures/uploads/menu-w47.jpg?url";

export interface ExampleDoc {
  filename: string;
  title: string;
  sub: string;
  url: string;
  type: string;
}

export const EXAMPLE_DOCS: ExampleDoc[] = [
  { filename: "menu-w47.jpg", title: "Lunch menu, week 47", sub: "photo · menu-w47.jpg", url: menu, type: "image/jpeg" },
  { filename: "allergy-note.jpg", title: "Allergy note", sub: "photo · fridge door", url: allergyNote, type: "image/jpeg" },
  { filename: "invoices-oct.pdf", title: "Invoices", sub: "PDF · Sep–Oct · 8 deliveries", url: invoices, type: "application/pdf" },
];

/** The bundled example file as a File, ready for `POST /uploads`. Static asset, not a backend call. */
export async function exampleFile(doc: ExampleDoc): Promise<File> {
  const res = await fetch(doc.url);
  if (!res.ok) throw new Error(`example ${doc.filename} missing`);
  return new File([await res.blob()], doc.filename, { type: doc.type });
}
