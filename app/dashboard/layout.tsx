"use client";

import { useEffect, useState, type ReactNode } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  ArrowUpRight,
  BarChart3,
  Globe,
  LayoutDashboard,
  LogOut,
  Mail,
  Menu,
  ScanSearch,
  Search,
  Settings,
  Swords,
  TrendingUp,
  Users,
  X,
} from "lucide-react";
import { Logo } from "@/components/logo";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { businessOrDemo, hasOnboarded } from "@/lib/business-store";
import { DEMO_BUSINESS } from "@/lib/demo";
import type { BusinessInput } from "@/lib/types";
import { cn, initials } from "@/lib/utils";

interface NavItem {
  label: string;
  href: string;
  icon: typeof LayoutDashboard;
}

const NAV_SECTIONS: { label: string; items: NavItem[] }[] = [
  {
    label: "Workspace",
    items: [{ label: "Overview", href: "/dashboard", icon: LayoutDashboard }],
  },
  {
    label: "AI Agents",
    items: [
      { label: "AI Audit", href: "/dashboard/audit", icon: ScanSearch },
      { label: "Competitors", href: "/dashboard/competitors", icon: Swords },
      { label: "Leads", href: "/dashboard/leads", icon: Users },
      { label: "Outreach", href: "/dashboard/outreach", icon: Mail },
      {
        label: "Opportunities",
        href: "/dashboard/opportunities",
        icon: TrendingUp,
      },
    ],
  },
  {
    label: "Insights",
    items: [
      { label: "Analytics", href: "/dashboard/analytics", icon: BarChart3 },
      { label: "Settings", href: "/dashboard/settings", icon: Settings },
    ],
  },
];

function SidebarNav({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  return (
    <nav className="flex-1 space-y-6 overflow-y-auto px-3 py-5 scrollbar-none">
      {NAV_SECTIONS.map((section) => (
        <div key={section.label}>
          <p className="mb-1.5 px-3 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
            {section.label}
          </p>
          <div className="space-y-0.5">
            {section.items.map((item) => {
              const active =
                item.href === "/dashboard"
                  ? pathname === "/dashboard"
                  : pathname.startsWith(item.href);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  onClick={onNavigate}
                  className={cn(
                    "flex items-center gap-2.5 rounded-md px-3 py-2 text-[13px] font-medium transition-colors",
                    active
                      ? "bg-primary/10 text-primary"
                      : "text-muted-foreground hover:bg-muted hover:text-foreground"
                  )}
                >
                  <item.icon className="h-4 w-4" />
                  {item.label}
                </Link>
              );
            })}
          </div>
        </div>
      ))}
    </nav>
  );
}

function PlanCard() {
  return (
    <div className="border-t p-3">
      <div className="rounded-lg border bg-surface p-3">
        <div className="flex items-center justify-between">
          <p className="text-[13px] font-semibold text-foreground">Growth plan</p>
          <Badge variant="accent" className="text-[10px]">
            trial
          </Badge>
        </div>
        <p className="mt-1 text-xs text-muted-foreground">
          14 days left — keep your agents running.
        </p>
        <Button asChild size="sm" className="mt-2.5 w-full gap-1">
          <Link href="/#pricing">
            Upgrade
            <ArrowUpRight className="h-3.5 w-3.5" />
          </Link>
        </Button>
      </div>
    </div>
  );
}

export default function DashboardLayout({ children }: { children: ReactNode }) {
  const [mounted, setMounted] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [business, setBusiness] = useState<BusinessInput>(DEMO_BUSINESS);
  const [onboarded, setOnboarded] = useState(true);
  const pathname = usePathname();

  useEffect(() => {
    setBusiness(businessOrDemo());
    setOnboarded(hasOnboarded());
    setMounted(true);
  }, [pathname]);

  useEffect(() => {
    setSidebarOpen(false);
  }, [pathname]);

  const websiteHost = business.website_url.replace(/^https?:\/\//, "");

  return (
    <div className="min-h-screen bg-surface">
      {/* Mobile overlay */}
      {sidebarOpen ? (
        <button
          aria-label="Close sidebar"
          className="fixed inset-0 z-40 bg-secondary/40 backdrop-blur-sm lg:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      ) : null}

      {/* Sidebar */}
      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-50 flex w-60 flex-col border-r bg-background transition-transform duration-200 lg:translate-x-0",
          sidebarOpen ? "translate-x-0" : "-translate-x-full"
        )}
      >
        <div className="flex h-14 items-center justify-between border-b px-4">
          <Logo href="/dashboard" />
          <button
            className="rounded-md p-1 text-muted-foreground hover:bg-muted lg:hidden"
            onClick={() => setSidebarOpen(false)}
            aria-label="Close menu"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <SidebarNav onNavigate={() => setSidebarOpen(false)} />
        <PlanCard />
      </aside>

      {/* Main column */}
      <div className="flex min-h-screen flex-col lg:pl-60">
        {/* Top bar */}
        <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b bg-background/80 px-4 backdrop-blur sm:px-6">
          <button
            className="rounded-md p-1.5 text-muted-foreground hover:bg-muted lg:hidden"
            onClick={() => setSidebarOpen(true)}
            aria-label="Open menu"
          >
            <Menu className="h-5 w-5" />
          </button>

          <div className="flex min-w-0 items-center gap-2.5">
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold leading-tight text-foreground">
                {mounted ? business.name : "GrowthOS"}
              </p>
              <p className="hidden items-center gap-1 text-xs leading-tight text-muted-foreground sm:flex">
                <Globe className="h-3 w-3" />
                <span className="truncate">{mounted ? websiteHost : ""}</span>
              </p>
            </div>
            {mounted && !onboarded ? (
              <Badge variant="warning" className="shrink-0 text-[10px]">
                Demo data
              </Badge>
            ) : null}
          </div>

          <div className="ml-auto flex items-center gap-3">
            <div className="relative hidden md:block">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
              <input
                type="search"
                placeholder="Search leads, campaigns..."
                className="h-8 w-56 rounded-md border border-input bg-surface pl-8 pr-3 text-[13px] shadow-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
              />
            </div>

            <DropdownMenu>
              <DropdownMenuTrigger className="rounded-full focus:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                <Avatar className="h-8 w-8 border">
                  <AvatarFallback>
                    {mounted ? initials(business.name) : "GO"}
                  </AvatarFallback>
                </Avatar>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-52">
                <DropdownMenuLabel className="font-normal">
                  <p className="text-sm font-medium">
                    {mounted ? business.name : "GrowthOS"}
                  </p>
                  <p className="text-xs text-muted-foreground">Growth plan · trial</p>
                </DropdownMenuLabel>
                <DropdownMenuSeparator />
                <DropdownMenuItem asChild>
                  <Link href="/dashboard/settings">
                    <Settings className="h-4 w-4" />
                    Settings
                  </Link>
                </DropdownMenuItem>
                <DropdownMenuItem asChild>
                  <Link href="/">
                    <Globe className="h-4 w-4" />
                    Back to site
                  </Link>
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem asChild>
                  <Link href="/login">
                    <LogOut className="h-4 w-4" />
                    Sign out
                  </Link>
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </header>

        <main className="flex-1 p-6 lg:p-8">{children}</main>
      </div>
    </div>
  );
}
