import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";

const inter = Inter({ subsets: ["latin"], variable: "--font-sans" });
const mono = JetBrains_Mono({ subsets: ["latin"], variable: "--font-mono" });

export const metadata: Metadata = {
  metadataBase: new URL(process.env.NEXT_PUBLIC_APP_URL ?? "https://growthos.app"),
  title: {
    default: "GrowthOS — The Revenue Operating System",
    template: "%s · GrowthOS",
  },
  description:
    "AI analyzes your business, competitors, website, and market to build a complete growth system in minutes. Find more customers. Close more deals. Grow faster.",
  openGraph: {
    title: "GrowthOS — The Revenue Operating System",
    description:
      "AI analyzes your business, competitors, website, and market to build a complete growth system in minutes.",
    type: "website",
    siteName: "GrowthOS",
  },
  twitter: {
    card: "summary_large_image",
    title: "GrowthOS — The Revenue Operating System",
    description:
      "Find more customers. Close more deals. Grow faster — with an AI growth team working 24/7.",
  },
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className={`${inter.variable} ${mono.variable}`}>
      <body className="min-h-screen font-sans">{children}</body>
    </html>
  );
}
