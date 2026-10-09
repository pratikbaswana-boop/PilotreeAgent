import {
  CheckCircledIcon,
  ExclamationTriangleIcon,
  LockClosedIcon,
} from "@radix-ui/react-icons";
import { copy, label } from "@/lib/copy";
export function SafetyBanner({
  decision,
  reasons = [],
}: {
  decision: string;
  reasons?: string[];
}) {
  const key =
    decision in copy.safety ? (decision as keyof typeof copy.safety) : "ALLOW";
  const content = copy.safety[key];
  const Icon =
    key === "BLOCK"
      ? LockClosedIcon
      : key === "ALLOW"
        ? CheckCircledIcon
        : ExclamationTriangleIcon;
  return (
    <section className={`safety-banner safety-${key}`} aria-live="polite">
      <Icon />
      <div>
        <strong>{content.title}</strong>
        <p>{content.text}</p>
        {reasons.length > 0 && (
          <p className="reason-codes">
            {[...new Set(reasons)].map(label).join(" · ")}
          </p>
        )}
      </div>
    </section>
  );
}
