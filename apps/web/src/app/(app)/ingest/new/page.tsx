"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { api, getErrorMessage } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export default function NewIngestPage() {
  const router = useRouter();
  const sources = useQuery({ queryKey: ["sources"], queryFn: api.listSources });
  const [sourceId, setSourceId] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!sourceId || !file) return;
    setError(null);
    setUploading(true);
    try {
      const res = await api.uploadFile(sourceId, file);
      router.push(`/ingest/${res.job_id}`);
    } catch (err) {
      setError(getErrorMessage(err, "Upload failed"));
    } finally {
      setUploading(false);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Upload a file</h1>
        <p className="text-sm text-muted-foreground">
          CSV, JSON, or JSONL — one record per row. Required column: <code>text</code>.
          Optional: <code>author</code>, <code>created</code>, <code>id</code>, <code>language</code>.
        </p>
      </div>

      <Card>
        <CardHeader><CardTitle>New upload</CardTitle></CardHeader>
        <CardContent>
          {sources.data?.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              Create a <a className="underline" href="/sources">source</a> first.
            </p>
          ) : (
            <form onSubmit={onSubmit} className="space-y-4">
              <div>
                <label className="mb-1 block text-sm font-medium">Source</label>
                <select
                  required
                  value={sourceId}
                  onChange={(e) => setSourceId(e.target.value)}
                  className="w-full rounded-md border border-border bg-transparent px-3 py-2 text-sm"
                >
                  <option value="">Select…</option>
                  {sources.data?.map((s) => (
                    <option key={s.id} value={s.id}>{s.name} ({s.source_type})</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="mb-1 block text-sm font-medium">File</label>
                <Input
                  type="file"
                  accept=".csv,.json,.jsonl"
                  required
                  onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                />
              </div>
              {error && <p className="text-sm text-destructive">{error}</p>}
              <Button type="submit" disabled={uploading || !sourceId || !file}>
                {uploading ? "Uploading…" : "Upload and process"}
              </Button>
            </form>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
