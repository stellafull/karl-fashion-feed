import { useRef, useState } from "react";
import { Plus, Sparkles, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import type { PendingImageAttachment } from "@/hooks/useImageAttachments";
import { cn } from "@/lib/utils";

interface ChatComposerProps {
  draft: string;
  onDraftChange: (value: string) => void;
  onSubmit: () => void | Promise<void>;
  placeholder: string;
  statusLabel: string;
  submitLabel: string;
  submittingLabel: string;
  isSubmitting?: boolean;
  attachments: PendingImageAttachment[];
  onAppendFiles: (files: File[]) => void;
  onRemoveAttachment: (attachmentId: string) => void;
  onResetFileInput?: () => void;
  className?: string;
  shellClassName?: string;
  submitButtonClassName?: string;
}

export default function ChatComposer({
  draft,
  onDraftChange,
  onSubmit,
  placeholder,
  statusLabel,
  submitLabel,
  submittingLabel,
  isSubmitting = false,
  attachments,
  onAppendFiles,
  onRemoveAttachment,
  onResetFileInput,
  className,
  shellClassName,
  submitButtonClassName,
}: ChatComposerProps) {
  const [isDraggingFiles, setIsDraggingFiles] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const dragDepthRef = useRef(0);

  const attachmentLabel = isDraggingFiles
    ? "Drop images to attach"
    : attachments.length > 0
      ? `${attachments.length} image${attachments.length > 1 ? "s" : ""} attached`
      : statusLabel;

  const openFilePicker = () => {
    fileInputRef.current?.click();
  };

  return (
    <div className={cn("pointer-events-none absolute inset-x-0 bottom-0 z-20 px-4 pb-5 pt-2 md:px-8", className)}>
      <div className="mx-auto max-w-4xl pointer-events-auto">
        <div
          className={cn(
            "ff-motion-panel rounded-[28px] border bg-[rgba(255,253,249,0.94)] p-3 shadow-[0_18px_48px_rgba(44,33,16,0.08)] backdrop-blur-xl",
            isDraggingFiles
              ? "border-dashed border-[#9f7d45] bg-[#fbf7ef]"
              : "border-[#e7decf]",
            shellClassName
          )}
          onDragEnter={(event) => {
            if (!Array.from(event.dataTransfer.items).some((item) => item.kind === "file")) {
              return;
            }

            dragDepthRef.current += 1;
            setIsDraggingFiles(true);
          }}
          onDragOver={(event) => {
            event.preventDefault();
          }}
          onDragLeave={() => {
            dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
            if (dragDepthRef.current === 0) {
              setIsDraggingFiles(false);
            }
          }}
          onDrop={(event) => {
            event.preventDefault();
            dragDepthRef.current = 0;
            setIsDraggingFiles(false);
            onAppendFiles(Array.from(event.dataTransfer.files));
          }}
        >
          <input
            ref={fileInputRef}
            type="file"
            accept="image/*"
            multiple
            className="hidden"
            onChange={(event) => {
              onAppendFiles(Array.from(event.target.files ?? []));
              onResetFileInput?.();
              if (fileInputRef.current) {
                fileInputRef.current.value = "";
              }
            }}
          />
          {attachments.length > 0 && (
            <div className="grid grid-cols-3 gap-2 px-2 pb-3 sm:grid-cols-5">
              {attachments.map((attachment) => (
                <div
                  key={attachment.id}
                  className="group ff-motion-soft relative overflow-hidden rounded-2xl border border-[#e4dccf] bg-[#faf7f1]"
                >
                  <img
                    src={attachment.previewUrl}
                    alt={attachment.file.name}
                    className="aspect-square w-full object-cover"
                  />
                  <button
                    type="button"
                    className="ff-motion-soft absolute right-2 top-2 flex h-7 w-7 items-center justify-center rounded-full bg-black/60 text-white opacity-100 sm:opacity-0 sm:group-hover:opacity-100"
                    aria-label={`Remove ${attachment.file.name}`}
                    onClick={() => onRemoveAttachment(attachment.id)}
                  >
                    <X className="h-4 w-4" />
                  </button>
                </div>
              ))}
            </div>
          )}
          <Textarea
            value={draft}
            onChange={(event) => onDraftChange(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                void onSubmit();
              }
            }}
            placeholder={placeholder}
            className="min-h-24 max-h-56 resize-none overflow-y-auto border-none bg-transparent px-3 py-3 text-base shadow-none focus-visible:ring-0"
          />
          <div className="flex items-center justify-between gap-3 px-2 pb-1 pt-2">
            <div className="flex items-center gap-2 text-sm text-[#7a7369]">
              <button
                type="button"
                className="ff-motion-soft flex h-10 w-10 items-center justify-center rounded-full border border-[#ddd4c7] bg-white/80 text-[#7a7369] hover:border-[#c8b18a] hover:text-[#2b241d]"
                aria-label="Upload image"
                onClick={openFilePicker}
              >
                <Plus className="h-4 w-4" />
              </button>
              <span className="rounded-full bg-[#f4efe5] px-3 py-1">{attachmentLabel}</span>
            </div>
            <Button
              className={cn(
                "ff-motion-soft rounded-full bg-[#1f1c18] px-5 text-[#f7f3eb] hover:bg-[#2c2721]",
                submitButtonClassName
              )}
              onClick={() => void onSubmit()}
              disabled={isSubmitting}
            >
              <Sparkles className="h-4 w-4" />
              {isSubmitting ? submittingLabel : submitLabel}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
