interface Props {
  notes: string[];
  title?: string;
}

/** Progressive disclosure for caveats — replaces the always-open “Read this first” wall. */
export function DataNotes({ notes, title = "Data notes" }: Props) {
  if (!notes.length) return null;
  return (
    <details className="data-notes">
      <summary>{title}</summary>
      <div className="data-notes-body">
        {notes.map((n) => (
          <p key={n}>{n}</p>
        ))}
      </div>
    </details>
  );
}
