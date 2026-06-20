import * as React from "react";
import { cn } from "@/lib/utils";

export function Card({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "rounded-xl border border-edge bg-panel/80 backdrop-blur-sm",
        className,
      )}
      {...props}
    />
  );
}

export function CardHeader({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("p-4 pb-2", className)} {...props} />;
}

export function CardBody({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("p-4 pt-2", className)} {...props} />;
}

export function Badge({
  className,
  tone = "neutral",
  ...props
}: React.HTMLAttributes<HTMLSpanElement> & {
  tone?: "neutral" | "ok" | "flag" | "halt" | "accent";
}) {
  const tones: Record<string, string> = {
    neutral: "bg-panel2 text-muted border-edge",
    ok: "bg-ok/10 text-ok border-ok/30",
    flag: "bg-flag/10 text-flag border-flag/30",
    halt: "bg-halt/10 text-halt border-halt/30",
    accent: "bg-accent/10 text-accent border-accent/30",
  };
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium",
        tones[tone],
        className,
      )}
      {...props}
    />
  );
}

type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "ghost" | "danger" | "outline";
  size?: "sm" | "md";
};

export function Button({
  className,
  variant = "primary",
  size = "md",
  ...props
}: ButtonProps) {
  const variants: Record<string, string> = {
    primary: "bg-accent text-white hover:bg-accent/90",
    ghost: "text-muted hover:text-ink hover:bg-panel2",
    outline: "border border-edge text-ink hover:bg-panel2",
    danger: "bg-halt text-white hover:bg-halt/90",
  };
  const sizes: Record<string, string> = {
    sm: "h-7 px-2.5 text-xs",
    md: "h-9 px-3.5 text-sm",
  };
  return (
    <button
      className={cn(
        "inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50",
        variants[variant],
        sizes[size],
        className,
      )}
      {...props}
    />
  );
}

export function StatusDot({ hex, pulse }: { hex: string; pulse?: boolean }) {
  return (
    <span className="relative inline-flex h-2.5 w-2.5">
      {pulse && (
        <span
          className="absolute inline-flex h-full w-full animate-ping rounded-full opacity-60"
          style={{ backgroundColor: hex }}
        />
      )}
      <span
        className="relative inline-flex h-2.5 w-2.5 rounded-full"
        style={{ backgroundColor: hex }}
      />
    </span>
  );
}

export function Stat({
  label,
  value,
  hint,
}: {
  label: string;
  value: React.ReactNode;
  hint?: string;
}) {
  return (
    <div className="rounded-lg border border-edge bg-panel2/60 px-3 py-2">
      <div className="text-[11px] uppercase tracking-wide text-muted">{label}</div>
      <div className="mt-0.5 text-lg font-semibold text-ink">{value}</div>
      {hint && <div className="text-[11px] text-muted">{hint}</div>}
    </div>
  );
}

export function Skeleton({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn("animate-pulse rounded-md bg-panel2", className)} {...props} />
  );
}

export function Spinner({ size = 16, className }: { size?: number; className?: string }) {
  return (
    <span
      role="status"
      aria-label="Loading"
      className={cn(
        "inline-block animate-spin rounded-full border-2 border-edge border-t-accent",
        className,
      )}
      style={{ width: size, height: size }}
    />
  );
}

export function Progress({
  value,
  tone = "#3b82f6",
  className,
}: {
  value: number;
  tone?: string;
  className?: string;
}) {
  const clamped = Math.min(1, Math.max(0, value));
  return (
    <div className={cn("h-1.5 w-full overflow-hidden rounded-full bg-panel2", className)}>
      <div
        className="h-full rounded-full transition-all"
        style={{ width: `${Math.round(clamped * 100)}%`, backgroundColor: tone }}
      />
    </div>
  );
}

export function Avatar({
  name,
  hex = "#3b82f6",
  size = 28,
}: {
  name: string;
  hex?: string;
  size?: number;
}) {
  const initials = name
    .split(/\s+/)
    .map((w) => w[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();
  return (
    <span
      className="inline-flex items-center justify-center rounded-full font-medium text-white"
      style={{ width: size, height: size, backgroundColor: hex, fontSize: size * 0.4 }}
    >
      {initials}
    </span>
  );
}

export function Separator({
  orientation = "horizontal",
  className,
}: {
  orientation?: "horizontal" | "vertical";
  className?: string;
}) {
  return (
    <div
      className={cn(
        "bg-edge",
        orientation === "horizontal" ? "h-px w-full" : "h-full w-px",
        className,
      )}
    />
  );
}

export function IconButton({
  className,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      className={cn(
        "inline-flex h-8 w-8 items-center justify-center rounded-lg text-muted transition-colors hover:bg-panel2 hover:text-ink disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      {...props}
    />
  );
}

export function Kbd({ children }: { children: React.ReactNode }) {
  return (
    <kbd className="inline-flex min-w-[20px] items-center justify-center rounded border border-edge bg-panel2 px-1.5 py-0.5 font-mono text-[10px] text-muted">
      {children}
    </kbd>
  );
}

export function EmptyState({
  icon,
  title,
  description,
  action,
}: {
  icon?: React.ReactNode;
  title: string;
  description?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-edge bg-panel/40 px-6 py-12 text-center">
      {icon && <div className="text-muted">{icon}</div>}
      <div className="text-sm font-medium text-ink">{title}</div>
      {description && <p className="max-w-sm text-xs text-muted">{description}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

export interface TabItem {
  value: string;
  label: React.ReactNode;
}

/** Controlled tabs — parent owns `value` so this stays render-only. */
export function Tabs({
  items,
  value,
  onValueChange,
  className,
}: {
  items: TabItem[];
  value: string;
  onValueChange: (v: string) => void;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "inline-flex gap-1 rounded-lg border border-edge bg-panel p-1",
        className,
      )}
    >
      {items.map((it) => (
        <button
          key={it.value}
          onClick={() => onValueChange(it.value)}
          className={cn(
            "rounded-md px-3 py-1 text-xs font-medium transition-colors",
            value === it.value
              ? "bg-accent/15 text-accent"
              : "text-muted hover:text-ink",
          )}
        >
          {it.label}
        </button>
      ))}
    </div>
  );
}

/** CSS-only hover tooltip (no state, safe anywhere). */
export function Tooltip({
  label,
  children,
}: {
  label: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <span className="group relative inline-flex">
      {children}
      <span className="pointer-events-none absolute bottom-full left-1/2 z-50 mb-1.5 -translate-x-1/2 whitespace-nowrap rounded-md border border-edge bg-panel2 px-2 py-1 text-[10px] text-ink opacity-0 shadow-lg transition-opacity group-hover:opacity-100">
        {label}
      </span>
    </span>
  );
}

export const Input = React.forwardRef<
  HTMLInputElement,
  React.InputHTMLAttributes<HTMLInputElement>
>(function Input({ className, ...props }, ref) {
  return (
    <input
      ref={ref}
      className={cn(
        "h-9 w-full rounded-lg border border-edge bg-panel2 px-3 text-sm text-ink placeholder:text-muted focus:border-accent focus:outline-none",
        className,
      )}
      {...props}
    />
  );
});

export const Textarea = React.forwardRef<
  HTMLTextAreaElement,
  React.TextareaHTMLAttributes<HTMLTextAreaElement>
>(function Textarea({ className, ...props }, ref) {
  return (
    <textarea
      ref={ref}
      className={cn(
        "w-full rounded-lg border border-edge bg-panel2 px-3 py-2 text-sm text-ink placeholder:text-muted focus:border-accent focus:outline-none",
        className,
      )}
      {...props}
    />
  );
});
