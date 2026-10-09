import { useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Textarea } from "./ui/textarea";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "./ui/dialog";
import { request, type Enquiry } from "@/lib/api";
export function NewEnquiry() {
  const [open, setOpen] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const navigate = useNavigate(),
    query = useQueryClient();
  return (
    <>
      <Button
        onClick={() => {
          setError("");
          setOpen(true);
        }}
      >
        New enquiry
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="triage-dialog">
          <DialogHeader>
            <DialogTitle>Create an enquiry</DialogTitle>
            <DialogDescription>
              Paste the original message, then run an AI analysis from its
              detail panel.
            </DialogDescription>
          </DialogHeader>
          <form
            onSubmit={async (event) => {
              event.preventDefault();
              const data = new FormData(event.currentTarget);
              setBusy(true);
              setError("");
              try {
                const enquiry = await request<Enquiry>("/enquiries", "POST", {
                  id: `ENQ-${crypto.randomUUID().slice(0, 8).toUpperCase()}`,
                  name: data.get("name"),
                  email: data.get("email"),
                  company: data.get("company"),
                  message: data.get("message"),
                  status: "new",
                });
                await query.invalidateQueries({ queryKey: ["enquiries"] });
                setOpen(false);
                await navigate({
                  to: "/enquiries/$id",
                  params: { id: enquiry.id },
                });
              } catch (e) {
                setError(
                  e instanceof Error ? e.message : "Could not create enquiry",
                );
              } finally {
                setBusy(false);
              }
            }}
          >
            <fieldset disabled={busy}>
              <label>
                Contact name
                <Input name="name" maxLength={200} />
              </label>
              <label>
                Email
                <Input name="email" type="email" required maxLength={200} />
              </label>
              <label>
                Company
                <Input name="company" required maxLength={200} />
              </label>
              <label>
                Original message
                <Textarea name="message" required maxLength={5000} rows={6} />
              </label>
            </fieldset>
            {error && (
              <p className="error-note" role="alert">
                {error}
              </p>
            )}
            <DialogFooter className="mt-5">
              <Button
                type="button"
                variant="outline"
                onClick={() => setOpen(false)}
              >
                Cancel
              </Button>
              <Button type="submit" disabled={busy}>
                {busy ? "Creating…" : "Create enquiry"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </>
  );
}
