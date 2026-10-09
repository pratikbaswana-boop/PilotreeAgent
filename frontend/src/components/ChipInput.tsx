import { useState } from "react";
import { Input } from "./ui/input";
export function ChipInput({
  value,
  onChange,
  label,
}: {
  value: string[];
  onChange: (value: string[]) => void;
  label: string;
}) {
  const [draft, setDraft] = useState("");
  const commit = () => {
    const additions = draft
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    if (additions.length) {
      onChange([...new Set([...value, ...additions])].slice(0, 6));
      setDraft("");
    }
  };
  return (
    <div className="chip-input">
      <div>
        {value.map((item, i) => (
          <span key={`${i}-${item}`}>
            {item}
            <button
              type="button"
              aria-label={`Remove ${item}`}
              onClick={() => onChange(value.filter((_, index) => index !== i))}
            >
              ×
            </button>
          </span>
        ))}
      </div>
      <Input
        aria-label={label}
        value={draft}
        disabled={value.length >= 6}
        placeholder={
          value.length >= 6 ? "Maximum 6 items" : "Type an item and press Enter"
        }
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            commit();
          }
        }}
      />
    </div>
  );
}
