/**
 * Shared domain types for GrowthOS.
 * These are the canonical contracts between API routes, AI workflows,
 * the dashboard UI, and the database layer.
 */

// ---------- Business / onboarding ----------

export interface BusinessProfile {
  id: string;
  user_id: string;
  name: string;
  website_url: string;
  industry: string;
  location: string;
  revenue_goal: number; // annual, USD
  target_customer: string;
  created_at: string;
}

export type BusinessInput = Omit<BusinessProfile, "id" | "user_id" | "created_at">;

// ---------- Workflow runs ----------

export type WorkflowKind =
  | "audit"
  | "competitors"
  | "leads"
  | "outreach"
  | "opportunities";

export type WorkflowStatus = "queued" | "running" | "completed" | "failed";

export interface WorkflowStep {
  key: string;
  label: string;
  status: WorkflowStatus;
  detail?: string;
}

export interface WorkflowRun<TResult = unknown> {
  id: string;
  kind: WorkflowKind;
  status: WorkflowStatus;
  steps: WorkflowStep[];
  result: TResult | null;
  error?: string;
  created_at: string;
  completed_at?: string;
}

// ---------- Website audit ----------

export type Severity = "critical" | "high" | "medium" | "low";
export type AuditCategory = "UX" | "SEO" | "CRO" | "Messaging" | "Performance";

export interface AuditIssue {
  id: string;
  category: AuditCategory;
  severity: Severity;
  title: string;
  description: string;
  recommendation: string;
  estimated_monthly_impact: number; // USD
  effort: "low" | "medium" | "high";
}

export interface AuditScorecard {
  overall: number;
  ux: number;
  seo: number;
  cro: number;
  messaging: number;
  performance: number;
}

export interface AuditReport {
  url: string;
  analyzed_at: string;
  scorecard: AuditScorecard;
  summary: string;
  issues: AuditIssue[];
  quick_wins: string[];
  total_estimated_monthly_impact: number;
  pages_analyzed: { url: string; title: string }[];
}

// ---------- Competitor intelligence ----------

export interface Competitor {
  id: string;
  name: string;
  website: string;
  positioning: string;
  estimated_monthly_traffic: number;
  traffic_trend: "up" | "down" | "flat";
  pricing_strategy: string;
  key_offers: string[];
  strengths: string[];
  weaknesses: string[];
  threat_level: "high" | "medium" | "low";
}

export interface CompetitorReport {
  analyzed_at: string;
  market_summary: string;
  competitors: Competitor[];
  positioning_gaps: string[];
  recommendations: {
    title: string;
    description: string;
    impact: "high" | "medium" | "low";
  }[];
}

// ---------- ICP & leads ----------

export interface ICP {
  id: string;
  name: string;
  industry: string;
  company_size: string;
  region: string;
  pain_points: string[];
  buying_triggers: string[];
  decision_makers: string[];
}

export type LeadStatus =
  | "new"
  | "contacted"
  | "replied"
  | "qualified"
  | "meeting"
  | "won"
  | "lost";

export interface Lead {
  id: string;
  company: string;
  website: string;
  industry: string;
  company_size: string;
  location: string;
  contact_name: string;
  contact_title: string;
  contact_email: string;
  linkedin_url: string;
  score: number; // 0-100
  score_reasons: string[];
  deal_probability: number; // 0-1
  estimated_deal_value: number;
  status: LeadStatus;
  created_at: string;
}

export interface LeadReport {
  generated_at: string;
  icp: ICP;
  leads: Lead[];
  total_pipeline_value: number;
}

// ---------- Outreach ----------

export type ChannelKind = "email" | "linkedin";

export interface SequenceStep {
  step: number;
  channel: ChannelKind;
  delay_days: number;
  subject?: string;
  body: string;
}

export interface Campaign {
  id: string;
  name: string;
  lead_count: number;
  status: "draft" | "active" | "paused" | "completed";
  sequence: SequenceStep[];
  stats: CampaignStats;
  created_at: string;
}

export interface CampaignStats {
  sent: number;
  opened: number;
  replied: number;
  meetings: number;
  open_rate: number;
  reply_rate: number;
  meeting_rate: number;
}

// ---------- Growth opportunities ----------

export type OpportunityCategory =
  | "Pricing"
  | "Funnel"
  | "SEO"
  | "Market"
  | "Product"
  | "Outbound";

export interface GrowthOpportunity {
  id: string;
  category: OpportunityCategory;
  title: string;
  description: string;
  impact_score: number; // 0-100
  effort: "low" | "medium" | "high";
  estimated_annual_value: number;
  status: "open" | "in_progress" | "done" | "dismissed";
}

export interface OpportunityReport {
  generated_at: string;
  summary: string;
  opportunities: GrowthOpportunity[];
  total_estimated_annual_value: number;
}

// ---------- Overview / analytics ----------

export interface OverviewMetrics {
  revenue_opportunity: number;
  new_leads_this_week: number;
  pipeline_value: number;
  conversion_score: number;
  competitor_score: number;
  growth_score: number;
  forecast: { month: string; baseline: number; with_growthos: number }[];
  activity: { id: string; agent: string; message: string; at: string }[];
}

export interface AnalyticsData {
  funnel: { stage: string; count: number }[];
  pipeline_by_month: { month: string; pipeline: number; closed: number }[];
  campaign_performance: {
    campaign: string;
    sent: number;
    open_rate: number;
    reply_rate: number;
    meetings: number;
  }[];
  lead_sources: { source: string; count: number }[];
  conversion_rate_trend: { week: string; rate: number }[];
}

// ---------- API envelopes ----------

export interface ApiError {
  error: string;
}

export interface PlanLimits {
  plan: "starter" | "growth" | "pro" | "enterprise";
  audits_per_month: number;
  leads_per_month: number;
  campaigns: number;
  seats: number;
}
