"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  api,
  getErrorMessage,
  type BrokerAccount,
  type BrokerFill,
  type BrokerOrder,
  type BrokerPosition,
  type BrokerStatus,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

// ─────────────────────────────────────────────────────────────────────

function fmtMoney(n: number): string {
  return n.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function fmtPct(n: number): string {
  const sign = n >= 0 ? "+" : "";
  return `${sign}${(n * 100).toFixed(2)}%`;
}

function fmtRelative(iso: string | null): string {
  if (!iso) return "—";
  const ms = Date.now() - new Date(iso).getTime();
  if (ms < 60_000) return "just now";
  const m = Math.floor(ms / 60_000);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

// ─────────────────────────────────────────────────────────────────────
// Connect form — shown when no credentials exist
// ─────────────────────────────────────────────────────────────────────

function ConnectForm({ onConnected }: { onConnected: () => void }) {
  const [broker, setBroker] = useState("alpaca");
  const [label, setLabel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [paper, setPaper] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const connect = useMutation({
    mutationFn: () =>
      api.broker.connect({
        broker,
        label: label.trim() || `${broker} account`,
        api_key: apiKey.trim(),
        api_secret: apiSecret.trim(),
        paper,
      }),
    onSuccess: () => {
      setError(null);
      onConnected();
    },
    onError: (e) => setError(getErrorMessage(e, "Could not connect")),
  });

  return (
    <Card className="max-w-xl">
      <CardHeader>
        <CardTitle>Connect a paper account</CardTitle>
        <CardDescription>
          Enter your Alpaca paper-trading API keys. We verify them with a
          live call before saving. You can revoke access at any time.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-1.5">
          <label className="text-xs text-muted-foreground">Broker</label>
          <select
            value={broker}
            onChange={(e) => setBroker(e.target.value)}
            className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
          >
            <option value="alpaca">Alpaca</option>
          </select>
        </div>

        <div className="space-y-1.5">
          <label className="text-xs text-muted-foreground">
            Label (optional)
          </label>
          <Input
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="My paper account"
          />
        </div>

        <div className="space-y-1.5">
          <label className="text-xs text-muted-foreground">API key</label>
          <Input
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder="PK…"
            autoComplete="off"
          />
        </div>

        <div className="space-y-1.5">
          <label className="text-xs text-muted-foreground">API secret</label>
          <Input
            type="password"
            value={apiSecret}
            onChange={(e) => setApiSecret(e.target.value)}
            placeholder="…"
            autoComplete="off"
          />
        </div>

        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={paper}
            onChange={(e) => setPaper(e.target.checked)}
          />
          Use paper trading account
        </label>

        {error && (
          <p className="text-sm text-red-400 border border-red-500/30 rounded-md px-3 py-2 bg-red-500/5">
            {error}
          </p>
        )}

        <div className="flex justify-end">
          <Button
            onClick={() => connect.mutate()}
            disabled={
              connect.isPending ||
              !apiKey.trim() ||
              !apiSecret.trim()
            }
          >
            {connect.isPending ? "Verifying…" : "Connect"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Account dashboard — shown when connected
// ─────────────────────────────────────────────────────────────────────

function AccountSummary() {
  const q = useQuery({
    queryKey: ["broker-account"],
    queryFn: () => api.broker.account(),
    refetchInterval: 30_000,
  });
  if (q.isLoading) return <p className="text-sm text-muted-foreground">Loading account…</p>;
  if (q.isError)
    return (
      <p className="text-sm text-red-400">
        {getErrorMessage(q.error, "Could not fetch account")}
      </p>
    );
  const a: BrokerAccount | undefined = q.data;
  if (!a) return null;

  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <Card>
        <CardHeader className="pb-2">
          <CardDescription>Equity</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="text-2xl font-semibold tabular-nums">
            ${fmtMoney(a.equity)}
          </div>
        </CardContent>
      </Card>
      <Card>
        <CardHeader className="pb-2">
          <CardDescription>Buying power</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="text-2xl font-semibold tabular-nums">
            ${fmtMoney(a.buying_power)}
          </div>
        </CardContent>
      </Card>
      <Card>
        <CardHeader className="pb-2">
          <CardDescription>Cash</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="text-2xl font-semibold tabular-nums">
            ${fmtMoney(a.cash)}
          </div>
        </CardContent>
      </Card>
      <Card>
        <CardHeader className="pb-2">
          <CardDescription>Status</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="text-sm">
            <span className="font-medium">{a.status}</span>
            <span className="text-muted-foreground"> · {a.currency}</span>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

function PositionsTable() {
  const q = useQuery({
    queryKey: ["broker-positions"],
    queryFn: () => api.broker.positions(),
    refetchInterval: 30_000,
  });
  const rows: BrokerPosition[] = q.data ?? [];

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">Positions</CardTitle>
        <CardDescription>
          {q.isLoading ? "Loading…" : `${rows.length} open`}
        </CardDescription>
      </CardHeader>
      <CardContent>
        {q.isLoading ? null : rows.length === 0 ? (
          <p className="text-sm text-muted-foreground">No open positions.</p>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-xs text-muted-foreground">
              <tr>
                <th className="text-left py-2">Symbol</th>
                <th className="text-right">Qty</th>
                <th className="text-right">Avg entry</th>
                <th className="text-right">Market value</th>
                <th className="text-right">Unrealized P&L</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((p) => (
                <tr key={p.symbol} className="border-t border-border">
                  <td className="py-2 font-mono">{p.symbol}</td>
                  <td className="text-right tabular-nums">{p.qty}</td>
                  <td className="text-right tabular-nums">
                    ${fmtMoney(p.avg_entry_price)}
                  </td>
                  <td className="text-right tabular-nums">
                    ${fmtMoney(p.market_value)}
                  </td>
                  <td
                    className={
                      "text-right tabular-nums " +
                      (p.unrealized_pl >= 0 ? "text-green-400" : "text-red-400")
                    }
                  >
                    ${fmtMoney(p.unrealized_pl)} ({fmtPct(p.unrealized_plpc)})
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </CardContent>
    </Card>
  );
}

function OrdersTable() {
  const q = useQuery({
    queryKey: ["broker-orders"],
    queryFn: () => api.broker.orders(20),
    refetchInterval: 30_000,
  });
  const rows: BrokerOrder[] = q.data ?? [];

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">Recent orders</CardTitle>
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <p className="text-sm text-muted-foreground">No recent orders.</p>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-xs text-muted-foreground">
              <tr>
                <th className="text-left py-2">Symbol</th>
                <th className="text-left">Side</th>
                <th className="text-right">Qty</th>
                <th className="text-right">Filled</th>
                <th className="text-left">Status</th>
                <th className="text-right">Submitted</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((o) => (
                <tr key={o.id} className="border-t border-border">
                  <td className="py-2 font-mono">{o.symbol}</td>
                  <td
                    className={
                      o.side === "buy" ? "text-green-400" : "text-red-400"
                    }
                  >
                    {o.side}
                  </td>
                  <td className="text-right tabular-nums">{o.qty}</td>
                  <td className="text-right tabular-nums">
                    {o.filled_qty}
                  </td>
                  <td>{o.status}</td>
                  <td className="text-right text-xs text-muted-foreground">
                    {o.submitted_at
                      ? new Date(o.submitted_at).toLocaleString()
                      : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </CardContent>
    </Card>
  );
}

function FillsTable() {
  const q = useQuery({
    queryKey: ["broker-fills"],
    queryFn: () => api.broker.fills(),
    refetchInterval: 30_000,
  });
  const rows: BrokerFill[] = q.data ?? [];

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">Today's fills</CardTitle>
        <CardDescription>
          These are the trades the model reports in the market-daily article.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <p className="text-sm text-muted-foreground">No fills today.</p>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-xs text-muted-foreground">
              <tr>
                <th className="text-left py-2">Symbol</th>
                <th className="text-left">Side</th>
                <th className="text-right">Qty</th>
                <th className="text-right">Price</th>
                <th className="text-right">Time</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((f) => (
                <tr key={f.id} className="border-t border-border">
                  <td className="py-2 font-mono">{f.symbol}</td>
                  <td
                    className={
                      f.side === "buy" ? "text-green-400" : "text-red-400"
                    }
                  >
                    {f.side}
                  </td>
                  <td className="text-right tabular-nums">{f.qty}</td>
                  <td className="text-right tabular-nums">
                    ${fmtMoney(f.price)}
                  </td>
                  <td className="text-right text-xs text-muted-foreground">
                    {f.transaction_time
                      ? new Date(f.transaction_time).toLocaleTimeString()
                      : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </CardContent>
    </Card>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Order ticket — paper only
// ─────────────────────────────────────────────────────────────────────

function OrderTicket({ paper }: { paper: boolean }) {
  const qc = useQueryClient();
  const [symbol, setSymbol] = useState("");
  const [side, setSide] = useState<"buy" | "sell">("buy");
  const [qty, setQty] = useState("");
  const [type, setType] = useState<"market" | "limit">("market");
  const [limitPrice, setLimitPrice] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const place = useMutation({
    mutationFn: () =>
      api.broker.placeOrder({
        symbol: symbol.trim().toUpperCase(),
        side,
        qty: parseFloat(qty),
        type,
        limit_price: type === "limit" ? parseFloat(limitPrice) : undefined,
      }),
    onSuccess: (order) => {
      setError(null);
      setSuccess(
        `Order ${order.id.slice(0, 8)} — ${order.side} ${order.qty} ${order.symbol} (${order.status})`,
      );
      setSymbol("");
      setQty("");
      setLimitPrice("");
      qc.invalidateQueries({ queryKey: ["broker-orders"] });
    },
    onError: (e) => {
      setSuccess(null);
      setError(getErrorMessage(e, "Order failed"));
    },
  });

  const canSubmit =
    !place.isPending &&
    paper &&
    symbol.trim().length > 0 &&
    parseFloat(qty) > 0 &&
    (type === "market" || parseFloat(limitPrice) > 0);

  const onSubmit = () => {
    if (!paper) return;
    const summary = `${side.toUpperCase()} ${qty} ${symbol.toUpperCase()} ${
      type === "market" ? "at market" : `limit $${limitPrice}`
    }`;
    if (!confirm(`Place this paper order?

${summary}`)) return;
    setSuccess(null);
    setError(null);
    place.mutate();
  };

  if (!paper) {
    return (
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">Place an order</CardTitle>
          <CardDescription>
            Order placement is available for paper accounts only.
          </CardDescription>
        </CardHeader>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">Place a paper order</CardTitle>
        <CardDescription>
          Market or limit. A confirmation dialog appears before submission.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1.5">
            <label className="text-xs text-muted-foreground">Symbol</label>
            <Input
              value={symbol}
              onChange={(e) => setSymbol(e.target.value)}
              placeholder="XLK"
              autoComplete="off"
            />
          </div>
          <div className="space-y-1.5">
            <label className="text-xs text-muted-foreground">Side</label>
            <select
              value={side}
              onChange={(e) => setSide(e.target.value as "buy" | "sell")}
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
            >
              <option value="buy">Buy</option>
              <option value="sell">Sell</option>
            </select>
          </div>
          <div className="space-y-1.5">
            <label className="text-xs text-muted-foreground">Quantity</label>
            <Input
              value={qty}
              onChange={(e) => setQty(e.target.value)}
              placeholder="100"
              inputMode="numeric"
              autoComplete="off"
            />
          </div>
          <div className="space-y-1.5">
            <label className="text-xs text-muted-foreground">Type</label>
            <select
              value={type}
              onChange={(e) => setType(e.target.value as "market" | "limit")}
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
            >
              <option value="market">Market</option>
              <option value="limit">Limit</option>
            </select>
          </div>
        </div>

        {type === "limit" && (
          <div className="space-y-1.5">
            <label className="text-xs text-muted-foreground">Limit price</label>
            <Input
              value={limitPrice}
              onChange={(e) => setLimitPrice(e.target.value)}
              placeholder="199.50"
              inputMode="decimal"
              autoComplete="off"
            />
          </div>
        )}

        {error && (
          <p className="text-sm text-red-400 border border-red-500/30 rounded-md px-3 py-2 bg-red-500/5">
            {error}
          </p>
        )}
        {success && (
          <p className="text-sm text-green-400 border border-green-500/30 rounded-md px-3 py-2 bg-green-500/5">
            {success}
          </p>
        )}

        <div className="flex justify-end">
          <Button onClick={onSubmit} disabled={!canSubmit}>
            {place.isPending ? "Placing…" : "Place order"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}


// ─────────────────────────────────────────────────────────────────────
// Header — connection status + disconnect + monitor toggle
// ─────────────────────────────────────────────────────────────────────

function AccountHeader({
  status,
  onDisconnect,
}: {
  status: BrokerStatus;
  onDisconnect: () => void;
}) {
  const qc = useQueryClient();

  const toggle = useMutation({
    mutationFn: (enabled: boolean) => api.broker.settings(enabled),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["broker-status"] });
    },
  });

  const disconnect = useMutation({
    mutationFn: () => api.broker.disconnect(),
    onSuccess: () => onDisconnect(),
  });

  return (
    <div className="flex items-start justify-between gap-4">
      <div>
        <h1 className="text-2xl font-semibold">
          {status.label ?? "Paper Account"}
        </h1>
        <p className="text-sm text-muted-foreground">
          {status.broker} · {status.paper ? "paper" : "live"} ·{" "}
          {status.key_preview} · last sync{" "}
          {fmtRelative(status.last_sync_at)}
        </p>
        {status.last_sync_error && (
          <p className="text-xs text-amber-400 mt-1">
            Last error: {status.last_sync_error}
          </p>
        )}
      </div>
      <div className="flex items-center gap-2">
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={!!status.monitor_enabled}
            onChange={(e) => toggle.mutate(e.target.checked)}
          />
          Monitor trades
        </label>
        <Button
          variant="outline"
          onClick={() => {
            if (confirm("Disconnect this broker account?")) {
              disconnect.mutate();
            }
          }}
          disabled={disconnect.isPending}
        >
          Disconnect
        </Button>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Page
// ─────────────────────────────────────────────────────────────────────

export default function PaperAccountPage() {
  const qc = useQueryClient();
  const status = useQuery({
    queryKey: ["broker-status"],
    queryFn: () => api.broker.status(),
    refetchInterval: 60_000,
  });

  if (status.isLoading) {
    return <p className="text-sm text-muted-foreground">Loading…</p>;
  }

  if (!status.data?.connected) {
    return (
      <div className="space-y-6">
        <div>
          <h1 className="text-2xl font-semibold">Paper Account</h1>
          <p className="text-sm text-muted-foreground">
            Connect your broker to report real trades in market-daily articles.
          </p>
        </div>
        <ConnectForm
          onConnected={() => {
            qc.invalidateQueries({ queryKey: ["broker-status"] });
          }}
        />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <AccountHeader
        status={status.data}
        onDisconnect={() => {
          qc.invalidateQueries({ queryKey: ["broker-status"] });
        }}
      />
      <AccountSummary />
      <OrderTicket paper={!!status.data.paper} />
      <PositionsTable />
      <OrdersTable />
      <FillsTable />
    </div>
  );
}
