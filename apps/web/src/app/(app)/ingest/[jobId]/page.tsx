"use client";

import { useQuery } from "@tanstack/react-query";
import { useParams } from "next/navigation";
import Link from "next/link";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";

export default function JobPage() {
  const params = useParams<{ jobId: string }>();
  const job = useQuery({
    queryKey: ["job", params.jobId],
    queryFn: () => api.getJob(params.jobId),
    refetchInterval: (q) => {
      const s = q.state.data?.status;
      return s === "succeeded" || s === "failed" ? false : 1500;
    },
  });

  const j = job.data;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Ingestion job</h1>
          <p className="font-mono text-xs text-muted-foreground">{params.jobId}</p>
        </div>
        <Link href="/records"><Button variant="outline">View records</Button></Link>
      </div>

      <Card>
        <CardHeader><CardTitle>Status</CardTitle></CardHeader>
        <CardContent className="space-y-3">
          {!j ? (
            <p className="text-sm text-muted-foreground">Loading…</p>
          ) : (
            <>
              <div className="flex items-center gap-2 text-sm">
                <span className="font-medium">Status:</span>
                <span className={
                  j.status === "succeeded" ? "text-green-500" :
                  j.status === "failed" ? "text-destructive" : "text-muted-foreground"
                }>{j.status}</span>
              </div>
              <div className="text-sm"><span className="font-medium">Format:</span> {j.file_format}</div>
              <div className="text-sm"><span className="font-medium">Processed:</span> {j.processed_records}</div>
              <div className="text-sm"><span className="font-medium">Failed:</span> {j.failed_records}</div>
              {j.error_message && (
                <p className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
                  {j.error_message}
                </p>
              )}
              {j.status === "succeeded" && (
                <p className="text-sm text-green-500">
                  Normalization complete. <Link href="/records" className="underline">View records →</Link>
                </p>
              )}
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
