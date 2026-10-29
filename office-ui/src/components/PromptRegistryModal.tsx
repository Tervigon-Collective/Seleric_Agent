import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { PromptRegistry } from "./PromptRegistry";

export function PromptRegistryModal({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  return (
    <Dialog open={open} onOpenChange={(next) => { if (!next) onClose(); }}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Prompt registry</DialogTitle>
          <DialogDescription>Browse executive, diagnosis, and domain questions — click to edit in the input.</DialogDescription>
        </DialogHeader>
        <PromptRegistry onPicked={onClose} defaultChip="exec" />
      </DialogContent>
    </Dialog>
  );
}
