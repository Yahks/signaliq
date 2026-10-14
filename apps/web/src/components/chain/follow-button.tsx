"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bell, BellOff } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";

export function FollowButton({ chainId }: { chainId: string }) {
  const qc = useQueryClient();
  const status = useQuery({
    queryKey: ["follow", chainId],
    queryFn: () => api.getFollowStatus(chainId),
  });

  const follow = useMutation({
    mutationFn: () => api.followChain(chainId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["follow", chainId] });
      qc.invalidateQueries({ queryKey: ["notifications"] });
    },
  });

  const unfollow = useMutation({
    mutationFn: () => api.unfollowChain(chainId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["follow", chainId] });
      qc.invalidateQueries({ queryKey: ["notifications"] });
    },
  });

  if (status.isLoading) {
    return <Button variant="outline" disabled>…</Button>;
  }

  const following = status.data?.following ?? false;
  const busy = follow.isPending || unfollow.isPending;

  if (following) {
    return (
      <Button
        variant="outline"
        onClick={() => unfollow.mutate()}
        disabled={busy}
      >
        <BellOff className="h-4 w-4 mr-1.5" />
        Unfollow
      </Button>
    );
  }

  return (
    <Button
      variant="default"
      onClick={() => follow.mutate()}
      disabled={busy}
    >
      <Bell className="h-4 w-4 mr-1.5" />
      Follow for updates
    </Button>
  );
}
