// frontend/src/components/AudioUploader.jsx
// DVMELTSS-FIX: V - Validate, E - Error handling, A - Async
// ASCALE-FIX: S - Separation, M - Modular
import { useState, useRef, useCallback } from "react";
import toast from "react-hot-toast";
import PropTypes from "prop-types";

const AUDIO_EXTENSIONS = new Set(["mp3", "mp4", "wav", "m4a", "ogg", "webm"]);
const DOC_EXTENSIONS = new Set(["docx", "xlsx"]);
const MAX_FILE_SIZE_MB = 50;

const FORMAT_ICONS = {
  mp3:  "🎵", mp4:  "🎬", wav:  "🎙️",
  m4a:  "🎵", ogg:  "🎵", webm: "🎬",
  docx: "📝", xlsx: "📊",
};

const STAGES = [
  { key: "upload",   label: "Uploading",     color: "bg-blue-500" },
  { key: "process",  label: "Processing",    color: "bg-purple-500" },
  { key: "indexing", label: "Indexing",      color: "bg-teal-500" },
  { key: "done",     label: "Ready",         color: "bg-green-500" },
];

export function AudioUploader({ onSuccess, API_URL = import.meta.env?.VITE_API_URL || "" }) {
  const [stage, setStage] = useState(null);
  const [progress, setProgress] = useState(0);
  const [fileInfo, setFileInfo] = useState(null);
  const [error, setError] = useState(null);
  const inputRef = useRef(null);

  const getFileType = useCallback((filename) => {
    const ext = filename.split(".").pop()?.toLowerCase() || "";
    if (AUDIO_EXTENSIONS.has(ext)) return "audio";
    if (DOC_EXTENSIONS.has(ext)) return "document";
    return "other";
  }, []);

  const validateFile = useCallback((file) => {
    if (!file) return "No file selected";
    if (file.size > MAX_FILE_SIZE_MB * 1024 * 1024) {
      return `File too large. Maximum ${MAX_FILE_SIZE_MB}MB`;
    }
    const ext = file.name.split(".").pop()?.toLowerCase();
    const validTypes = [...AUDIO_EXTENSIONS, ...DOC_EXTENSIONS, "pdf", "png", "jpg", "jpeg", "tiff", "bmp"];
    if (!validTypes.includes(ext)) {
      return `Unsupported file type: .${ext}`;
    }
    return null;
  }, []);

  const handleFile = useCallback(async (file) => {
    const validationError = validateFile(file);
    if (validationError) {
      toast.error(validationError);
      setError(validationError);
      return;
    }

    const ext = file.name.split(".").pop()?.toLowerCase() || "";
    const icon = FORMAT_ICONS[ext] || "📄";
    const type = getFileType(file.name);

    setFileInfo({ name: file.name, size: file.size, icon, type, ext });
    setStage("upload");
    setProgress(0);
    setError(null);

    const formData = new FormData();
    formData.append("file", file);

    try {
      const xhr = new XMLHttpRequest();

      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) {
          setProgress(Math.round((e.loaded / e.total) * 100));
        }
      };

      xhr.onload = async () => {
        if (xhr.status === 200) {
          const data = JSON.parse(xhr.responseText);
          setStage("done");
          setProgress(100);
          toast.success(data.message || `${icon} ${file.name} indexed`);
          onSuccess?.(data);
        } else {
          const err = JSON.parse(xhr.responseText);
          throw new Error(err.detail || `HTTP ${xhr.status}`);
        }
      };

      xhr.onerror = () => {
        throw new Error("Upload failed");
      };

      setStage("process");
      xhr.open("POST", `${API_URL}/api/v1/ingest`);
      xhr.send(formData);

      // Simulate processing stage for audio
      if (type === "audio") {
        await new Promise(r => setTimeout(r, 1000));
        setStage("indexing");
      }

    } catch (err) {
      setStage(null);
      const errMsg = err.message || "Upload failed";
      setError(errMsg);
      toast.error(errMsg);
    }
  }, [validateFile, getFileType, onSuccess, API_URL]);

  const handleDrop = useCallback((e) => {
    e.preventDefault();
    const file = e.dataTransfer.files?.[0];
    if (file) handleFile(file);
  }, [handleFile]);

  const handleDragOver = useCallback((e) => {
    e.preventDefault();
  }, []);

  const currentStageIdx = STAGES.findIndex(s => s.key === stage);
  const currentStage = STAGES[currentStageIdx] || null;

  return (
    <div className="space-y-3" role="region" aria-label="File uploader">
      {/* Drop zone */}
      <div
        onDrop={handleDrop}
        onDragOver={handleDragOver}
        onClick={() => inputRef.current?.click()}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            inputRef.current?.click();
          }
        }}
        role="button"
        tabIndex={0}
        aria-label="Drop file or click to upload"
        className="border-2 border-dashed border-gray-300 dark:border-gray-600 rounded-xl p-6 text-center cursor-pointer hover:border-blue-400 dark:hover:border-blue-500 transition-colors focus:outline-none focus:ring-2 focus:ring-blue-500"
      >
        <input
          ref={inputRef}
          type="file"
          className="hidden"
          accept=".pdf,.png,.jpg,.jpeg,.tiff,.bmp,.docx,.xlsx,.mp3,.mp4,.wav,.m4a,.ogg,.webm"
          onChange={(e) => e.target.files?.[0] && handleFile(e.target.files[0])}
          aria-label="Select file to upload"
        />
        <div className="text-3xl mb-2" aria-hidden="true">
          {fileInfo ? fileInfo.icon : "📤"}
        </div>
        <p className="text-sm text-gray-600 dark:text-gray-400">
          {fileInfo ? fileInfo.name : "Drop any document or audio file"}
        </p>
        <p className="text-xs text-gray-400 mt-1">
          PDF · Image · Word · Excel · MP3 · MP4 · WAV
        </p>
        {error && (
          <p className="text-xs text-red-500 mt-2" role="alert">{error}</p>
        )}
      </div>

      {/* Progress */}
      {stage && stage !== "done" && (
        <div className="space-y-2" aria-live="polite">
          {/* Stage indicators */}
          <div className="flex justify-between">
            {STAGES.slice(0, -1).map((s, i) => (
              <div key={s.key} className="flex flex-col items-center gap-1">
                <div className={`
                  w-2.5 h-2.5 rounded-full transition-colors
                  ${i <= currentStageIdx
                    ? currentStage?.color || "bg-blue-500"
                    : "bg-gray-200 dark:bg-gray-700"
                  }
                `} aria-hidden="true" />
                <span className="text-[10px] text-gray-400">{s.label}</span>
              </div>
            ))}
          </div>

          {/* Progress bar */}
          <div className="h-1.5 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden" role="progressbar" aria-valuenow={progress} aria-valuemin={0} aria-valuemax={100}>
            <div
              className={`h-full rounded-full transition-all duration-300 ${currentStage?.color || "bg-blue-500"}`}
              style={{ width: `${progress}%` }}
            />
          </div>

          <p className="text-xs text-gray-500 dark:text-gray-400 text-center">
            {currentStage?.label}
            {fileInfo?.type === "audio" && stage === "process" && " — transcribing audio..."}
            {fileInfo?.type === "audio" && stage === "indexing" && " — embedding transcript..."}
          </p>
        </div>
      )}

      {/* Done */}
      {stage === "done" && fileInfo && (
        <div className="flex items-center gap-2 text-sm text-green-600 dark:text-green-400 bg-green-50 dark:bg-green-950/30 border border-green-200 dark:border-green-800 rounded-lg px-3 py-2" role="status">
          <span aria-hidden="true">✓</span>
          <span>{fileInfo.icon} {fileInfo.name} ready to query</span>
        </div>
      )}
    </div>
  );
}

AudioUploader.propTypes = {
  onSuccess: PropTypes.func,
  API_URL: PropTypes.string,
};