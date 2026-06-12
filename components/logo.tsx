import Link from "next/link";
import { cn } from "@/lib/utils";

export function LogoMark({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 32 32"
      fill="none"
      className={cn("h-7 w-7", className)}
      aria-hidden
    >
      <rect width="32" height="32" rx="8" fill="#0F172A" />
      <path
        d="M8 21.5 13.5 15l4 4L24 11"
        stroke="#2563EB"
        strokeWidth="2.75"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx="24" cy="11" r="2.4" fill="#14B8A6" />
    </svg>
  );
}

export function Logo({
  className,
  href = "/",
}: {
  className?: string;
  href?: string;
}) {
  return (
    <Link href={href} className={cn("flex items-center gap-2.5", className)}>
      <LogoMark />
      <span className="text-lg font-semibold tracking-tight text-foreground">
        GrowthOS
      </span>
    </Link>
  );
}
