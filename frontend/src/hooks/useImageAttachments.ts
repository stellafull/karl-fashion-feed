import { useEffect, useRef, useState } from "react";
import { nanoid } from "nanoid";
import type { AiAttachment } from "@/lib/ai-demo";

export interface PendingImageAttachment {
  id: string;
  file: File;
  previewUrl: string;
}

function isImageFile(file: File) {
  return file.type.startsWith("image/");
}

function readFileAsDataUrl(file: File) {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(typeof reader.result === "string" ? reader.result : "");
    reader.onerror = () => reject(reader.error ?? new Error(`Failed to read ${file.name}`));
    reader.readAsDataURL(file);
  });
}

export function useImageAttachments() {
  const [attachments, setAttachments] = useState<PendingImageAttachment[]>([]);
  const attachmentsRef = useRef<PendingImageAttachment[]>([]);

  useEffect(() => {
    attachmentsRef.current = attachments;
  }, [attachments]);

  useEffect(() => {
    return () => {
      for (const attachment of attachmentsRef.current) {
        URL.revokeObjectURL(attachment.previewUrl);
      }
    };
  }, []);

  const appendFiles = (files: File[]) => {
    const imageFiles = files.filter(isImageFile);
    if (imageFiles.length === 0) {
      return;
    }

    setAttachments((current) => {
      const existingKeys = new Set(
        current.map((attachment) => {
          const { file } = attachment;
          return `${file.name}-${file.size}-${file.lastModified}`;
        })
      );
      const next = [...current];

      for (const file of imageFiles) {
        const fileKey = `${file.name}-${file.size}-${file.lastModified}`;
        if (existingKeys.has(fileKey)) {
          continue;
        }

        existingKeys.add(fileKey);
        next.push({
          id: nanoid(),
          file,
          previewUrl: URL.createObjectURL(file),
        });
      }

      return next;
    });
  };

  const removeAttachment = (attachmentId: string) => {
    setAttachments((current) => {
      const attachment = current.find((item) => item.id === attachmentId);
      if (attachment) {
        URL.revokeObjectURL(attachment.previewUrl);
      }

      return current.filter((item) => item.id !== attachmentId);
    });
  };

  const buildOutgoingAttachments = async (): Promise<AiAttachment[]> => {
    return Promise.all(
      attachments.map(async (attachment) => ({
        id: attachment.id,
        name: attachment.file.name,
        mimeType: attachment.file.type,
        size: attachment.file.size,
        dataUrl: await readFileAsDataUrl(attachment.file),
      }))
    );
  };

  const resetAttachments = () => {
    setAttachments((current) => {
      for (const attachment of current) {
        URL.revokeObjectURL(attachment.previewUrl);
      }

      return [];
    });
  };

  return {
    attachments,
    appendFiles,
    removeAttachment,
    buildOutgoingAttachments,
    resetAttachments,
  };
}
