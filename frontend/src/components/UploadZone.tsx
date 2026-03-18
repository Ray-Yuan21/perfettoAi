interface Props {
  analysisState: "idle" | "uploading" | "analyzing" | "done" | "failed";
  dragOver: boolean;
  onDrop: (e: React.DragEvent) => void;
  onDragOver: (e: React.DragEvent) => void;
  onDragLeave: () => void;
  onFileSelected: (file: File) => void;
}

export default function UploadZone({
  analysisState,
  dragOver,
  onDrop,
  onDragOver,
  onDragLeave,
  onFileSelected,
}: Props) {
  return (
    <div
      className={`upload-zone ${dragOver ? "drag-over" : ""}`}
      onDrop={onDrop}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
    >
      <div className="upload-content">
        <div className="upload-icon">📂</div>
        <div className="upload-title">
          {analysisState === "uploading"
            ? "Uploading..."
            : "Drop a Perfetto trace file here"}
        </div>
        <div className="upload-subtitle">
          or{" "}
          <label className="upload-link">
            browse files
            <input
              type="file"
              accept=".perfetto-trace,.pb,.pftrace"
              hidden
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) {
                  onFileSelected(file);
                }
              }}
            />
          </label>
        </div>
        <div className="upload-hint">Supports .perfetto-trace, .pb, .pftrace</div>
      </div>
    </div>
  );
}
