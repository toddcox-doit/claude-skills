import { useState, useCallback } from "react";

const SYSTEM_PROMPT = `You are a PerfectScale technical coaching expert. You will receive an array of PerfectScale workload debug payloads (as a JSON array). Analyze all of them together and produce a structured batch coaching report.

Respond ONLY with valid JSON (no markdown, no backticks) in this exact shape:
{
  "summary": {
    "totalWorkloads": number,
    "cluster": "string",
    "customer": "string",
    "totalWaste": "string (e.g. '$2,341')",
    "totalBlockedRiskMitigation": "string (e.g. '+$891')",
    "topBlockerType": "string (most common CRD restriction)",
    "topIndicatorType": "string (most common indicator across workloads)",
    "narrative": "string (3-5 sentence executive summary)"
  },
  "workloads": [
    {
      "rank": number,
      "name": "string",
      "namespace": "string",
      "type": "string",
      "automationStatus": "string",
      "riskLevel": "string",
      "cost30d": "string",
      "waste30d": "string",
      "blockedRiskMitigation": "string",
      "savingsOpportunity": "string",
      "topIndicator": "string (e.g. 'OOM (sev 2)')",
      "primaryStory": "waste | risk | mixed",
      "headline": "string (one sharp sentence)",
      "currentResources": {
        "cpuRequest": "string", "cpuLimit": "string",
        "memRequest": "string", "memLimit": "string"
      },
      "recommendedResources": {
        "cpuRequest": "string", "cpuLimit": "string",
        "memRequest": "string", "memLimit": "string"
      },
      "indicators": [{"name": "string", "severity": number, "type": "waste | risk"}],
      "keyReasoningInsights": ["string (plain-English, 2-3 bullets max)"],
      "automationBlockers": [{"restriction": "string", "resource": "string", "effect": "string"}],
      "coachingPoints": ["string (customer-ready, 3 max)"],
      "topAction": "string (single most impactful change)"
    }
  ],
  "patterns": {
    "crdRestrictions": [{"rule": "string", "affectedWorkloads": ["string"], "count": number}],
    "indicators": [{"name": "string", "affectedWorkloads": ["string"], "count": number}],
    "automationSplit": {"limitedByUser": number, "limitedBySystem": number},
    "resourceGaps": ["string (plain-English observation about common gaps)"]
  },
  "unifiedActions": [
    {
      "action": "string",
      "affects": ["string (workload names)"],
      "affectsCount": number,
      "unlocksAmount": "string",
      "how": "string (specific kubectl/CRD step)"
    }
  ]
}`;

const COLORS = {
  bg: "#0f172a",
  card: "#1e293b",
  cardBorder: "#334155",
  accent: "#6366f1",
  accentLight: "#818cf8",
  green: "#22c55e",
  yellow: "#eab308",
  red: "#ef4444",
  orange: "#f97316",
  blue: "#3b82f6",
  text: "#f1f5f9",
  textMuted: "#94a3b8",
  textDim: "#64748b",
};

const sev = (n) => {
  if (n >= 3) return COLORS.red;
  if (n === 2) return COLORS.orange;
  if (n === 1) return COLORS.yellow;
  return COLORS.textMuted;
};

function Badge({ label, color, small }) {
  return (
    <span style={{
      background: color + "22",
      color,
      border: `1px solid ${color}44`,
      borderRadius: 4,
      padding: small ? "1px 6px" : "2px 8px",
      fontSize: small ? 11 : 12,
      fontWeight: 600,
      fontFamily: "monospace",
      whiteSpace: "nowrap",
    }}>{label}</span>
  );
}

function ResourceRow({ label, current, recommended }) {
  if (!current && !recommended) return null;
  const changed = current !== recommended && recommended && recommended !== "—";
  return (
    <tr>
      <td style={{ padding: "4px 8px", color: COLORS.textMuted, fontSize: 12, whiteSpace: "nowrap" }}>{label}</td>
      <td style={{ padding: "4px 8px", fontFamily: "monospace", fontSize: 12, color: COLORS.text }}>{current || "—"}</td>
      <td style={{ padding: "4px 8px", fontFamily: "monospace", fontSize: 12,
        color: changed ? COLORS.green : COLORS.textMuted }}>
        {changed ? `→ ${recommended}` : "no change"}
      </td>
    </tr>
  );
}

function WorkloadCard({ w, index }) {
  const [open, setOpen] = useState(false);
  const storyColor = w.primaryStory === "risk" ? COLORS.red : w.primaryStory === "mixed" ? COLORS.orange : COLORS.yellow;
  return (
    <div style={{
      background: COLORS.card,
      border: `1px solid ${COLORS.cardBorder}`,
      borderRadius: 8,
      marginBottom: 10,
      overflow: "hidden",
    }}>
      <div
        onClick={() => setOpen(o => !o)}
        style={{
          padding: "12px 16px",
          cursor: "pointer",
          display: "flex",
          alignItems: "center",
          gap: 12,
          background: open ? "#1e293bdd" : COLORS.card,
        }}
      >
        <span style={{
          background: storyColor + "22",
          color: storyColor,
          border: `1px solid ${storyColor}44`,
          borderRadius: 4,
          width: 24,
          height: 24,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          fontWeight: 700,
          fontSize: 12,
          flexShrink: 0,
        }}>{w.rank}</span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <span style={{ fontWeight: 700, color: COLORS.text, fontSize: 14 }}>{w.name}</span>
            <Badge label={w.namespace} color={COLORS.accentLight} small />
            <Badge label={w.type} color={COLORS.textDim} small />
            <Badge label={w.automationStatus} color={w.automationStatus?.includes("User") ? COLORS.orange : COLORS.yellow} small />
          </div>
          <div style={{ color: COLORS.textMuted, fontSize: 12, marginTop: 3 }}>{w.headline}</div>
        </div>
        <div style={{ display: "flex", gap: 16, flexShrink: 0, alignItems: "center" }}>
          <div style={{ textAlign: "right" }}>
            <div style={{ fontSize: 11, color: COLORS.textDim }}>Waste</div>
            <div style={{ fontWeight: 700, color: COLORS.yellow, fontSize: 14 }}>{w.waste30d}</div>
          </div>
          {w.blockedRiskMitigation && w.blockedRiskMitigation !== "$0" && (
            <div style={{ textAlign: "right" }}>
              <div style={{ fontSize: 11, color: COLORS.textDim }}>Risk Mit.</div>
              <div style={{ fontWeight: 700, color: COLORS.red, fontSize: 14 }}>{w.blockedRiskMitigation}</div>
            </div>
          )}
          <span style={{ color: COLORS.textDim, fontSize: 16 }}>{open ? "▲" : "▼"}</span>
        </div>
      </div>

      {open && (
        <div style={{ padding: "0 16px 16px", borderTop: `1px solid ${COLORS.cardBorder}` }}>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginTop: 12 }}>
            {/* Resources */}
            <div>
              <div style={{ fontSize: 11, color: COLORS.textDim, textTransform: "uppercase", letterSpacing: 1, marginBottom: 6 }}>Resources</div>
              <table style={{ width: "100%", borderCollapse: "collapse" }}>
                <tbody>
                  <ResourceRow label="CPU Request" current={w.currentResources?.cpuRequest} recommended={w.recommendedResources?.cpuRequest} />
                  <ResourceRow label="CPU Limit" current={w.currentResources?.cpuLimit} recommended={w.recommendedResources?.cpuLimit} />
                  <ResourceRow label="Mem Request" current={w.currentResources?.memRequest} recommended={w.recommendedResources?.memRequest} />
                  <ResourceRow label="Mem Limit" current={w.currentResources?.memLimit} recommended={w.recommendedResources?.memLimit} />
                </tbody>
              </table>
            </div>

            {/* Indicators */}
            <div>
              <div style={{ fontSize: 11, color: COLORS.textDim, textTransform: "uppercase", letterSpacing: 1, marginBottom: 6 }}>Indicators</div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                {(w.indicators || []).map((ind, i) => (
                  <Badge key={i} label={`${ind.name} (sev ${ind.severity})`} color={sev(ind.severity)} small />
                ))}
              </div>
              <div style={{ fontSize: 11, color: COLORS.textDim, textTransform: "uppercase", letterSpacing: 1, marginBottom: 6, marginTop: 12 }}>Automation Blockers</div>
              {(w.automationBlockers || []).map((b, i) => (
                <div key={i} style={{ fontSize: 12, color: COLORS.textMuted, marginBottom: 4 }}>
                  <span style={{ color: COLORS.orange, fontFamily: "monospace" }}>{b.restriction}</span>
                  {b.resource ? ` on ${b.resource}` : ""} — {b.effect}
                </div>
              ))}
            </div>
          </div>

          {/* Reasoning insights */}
          {w.keyReasoningInsights?.length > 0 && (
            <div style={{ marginTop: 12 }}>
              <div style={{ fontSize: 11, color: COLORS.textDim, textTransform: "uppercase", letterSpacing: 1, marginBottom: 6 }}>Key Reasoning</div>
              {w.keyReasoningInsights.map((ins, i) => (
                <div key={i} style={{ fontSize: 12, color: COLORS.textMuted, padding: "4px 0 4px 12px", borderLeft: `2px solid ${COLORS.accent}44`, marginBottom: 4 }}>{ins}</div>
              ))}
            </div>
          )}

          {/* Coaching points */}
          {w.coachingPoints?.length > 0 && (
            <div style={{ marginTop: 12 }}>
              <div style={{ fontSize: 11, color: COLORS.textDim, textTransform: "uppercase", letterSpacing: 1, marginBottom: 6 }}>Coaching Talking Points</div>
              {w.coachingPoints.map((pt, i) => (
                <div key={i} style={{ fontSize: 12, color: COLORS.text, padding: "4px 0 4px 12px", borderLeft: `2px solid ${COLORS.green}44`, marginBottom: 4 }}>"{pt}"</div>
              ))}
            </div>
          )}

          {/* Top action */}
          {w.topAction && (
            <div style={{
              marginTop: 12,
              background: COLORS.accent + "11",
              border: `1px solid ${COLORS.accent}33`,
              borderRadius: 6,
              padding: "8px 12px",
            }}>
              <span style={{ fontSize: 11, color: COLORS.accent, fontWeight: 700, textTransform: "uppercase", letterSpacing: 1 }}>Top Action: </span>
              <span style={{ fontSize: 13, color: COLORS.text }}>{w.topAction}</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function PatternsPanel({ patterns }) {
  if (!patterns) return null;
  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 20 }}>
      <div style={{ background: COLORS.card, border: `1px solid ${COLORS.cardBorder}`, borderRadius: 8, padding: 14 }}>
        <div style={{ fontSize: 11, color: COLORS.textDim, textTransform: "uppercase", letterSpacing: 1, marginBottom: 8 }}>CRD Restriction Patterns</div>
        {(patterns.crdRestrictions || []).map((r, i) => (
          <div key={i} style={{ marginBottom: 6 }}>
            <span style={{ fontFamily: "monospace", fontSize: 12, color: COLORS.orange }}>{r.rule}</span>
            <span style={{ fontSize: 12, color: COLORS.textMuted }}> — {r.count} workloads</span>
          </div>
        ))}
        <div style={{ marginTop: 10, fontSize: 12, color: COLORS.textMuted }}>
          Limited by User: <span style={{ color: COLORS.orange, fontWeight: 700 }}>{patterns.automationSplit?.limitedByUser ?? 0}</span>
          {" · "}Limited by System: <span style={{ color: COLORS.yellow, fontWeight: 700 }}>{patterns.automationSplit?.limitedBySystem ?? 0}</span>
        </div>
      </div>
      <div style={{ background: COLORS.card, border: `1px solid ${COLORS.cardBorder}`, borderRadius: 8, padding: 14 }}>
        <div style={{ fontSize: 11, color: COLORS.textDim, textTransform: "uppercase", letterSpacing: 1, marginBottom: 8 }}>Common Indicators</div>
        {(patterns.indicators || []).map((ind, i) => (
          <div key={i} style={{ fontSize: 12, color: COLORS.textMuted, marginBottom: 4 }}>
            <span style={{ color: COLORS.red }}>{ind.name}</span> — {ind.count} workloads
          </div>
        ))}
        {(patterns.resourceGaps || []).map((gap, i) => (
          <div key={i} style={{ fontSize: 12, color: COLORS.textDim, marginTop: 4, fontStyle: "italic" }}>{gap}</div>
        ))}
      </div>
    </div>
  );
}

function UnifiedActions({ actions }) {
  if (!actions?.length) return null;
  return (
    <div style={{ marginBottom: 20 }}>
      <h3 style={{ color: COLORS.text, fontSize: 14, marginBottom: 10 }}>Unified Recommended Actions</h3>
      {actions.map((a, i) => (
        <div key={i} style={{
          background: COLORS.card,
          border: `1px solid ${COLORS.cardBorder}`,
          borderRadius: 8,
          padding: "12px 14px",
          marginBottom: 8,
          display: "grid",
          gridTemplateColumns: "auto 1fr",
          gap: "8px 14px",
          alignItems: "start",
        }}>
          <span style={{
            background: COLORS.accent + "22",
            color: COLORS.accentLight,
            border: `1px solid ${COLORS.accent}44`,
            borderRadius: 4,
            width: 22,
            height: 22,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontWeight: 700,
            fontSize: 11,
            flexShrink: 0,
            marginTop: 1,
          }}>{i + 1}</span>
          <div>
            <div style={{ fontWeight: 600, color: COLORS.text, fontSize: 13 }}>{a.action}</div>
            <div style={{ fontSize: 12, color: COLORS.textMuted, marginTop: 3 }}>
              Affects <span style={{ color: COLORS.accentLight }}>{a.affectsCount} workloads</span>
              {a.unlocksAmount ? <> · Unlocks <span style={{ color: COLORS.green }}>{a.unlocksAmount}</span></> : null}
            </div>
            {a.affects?.length > 0 && (
              <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 4 }}>
                {a.affects.map((wl, j) => <Badge key={j} label={wl} color={COLORS.textDim} small />)}
              </div>
            )}
            {a.how && (
              <div style={{
                fontFamily: "monospace",
                fontSize: 11,
                color: COLORS.textMuted,
                background: "#0f172a",
                borderRadius: 4,
                padding: "4px 8px",
                marginTop: 6,
              }}>{a.how}</div>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

export default function BatchReport() {
  const [rawInput, setRawInput] = useState("");
  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [activeTab, setActiveTab] = useState("matrix");

  const analyze = useCallback(async () => {
    setLoading(true);
    setError(null);
    setReport(null);
    try {
      let jsonText = rawInput.trim();
      // Accept either a JSON array of payloads or a single object
      JSON.parse(jsonText); // validate
      let rawText;
      if (window.cowork?.askClaude) {
        rawText = await window.cowork.askClaude(
          `${SYSTEM_PROMPT}\n\nAnalyze these PerfectScale debug payloads and respond with valid JSON only:\n\n${jsonText}`
        );
      } else {
        const response = await fetch("https://api.anthropic.com/v1/messages", {
          method: "POST",
          headers: { "Content-Type": "application/json", "anthropic-version": "2023-06-01" },
          body: JSON.stringify({
            model: "claude-sonnet-4-6",
            max_tokens: 8000,
            system: SYSTEM_PROMPT,
            messages: [{ role: "user", content: `Analyze these PerfectScale debug payloads:\n\n${jsonText}` }]
          })
        });
        const data = await response.json();
        rawText = data.content?.find(b => b.type === "text")?.text || "";
      }
      let clean = (rawText || "").replace(/```json|```/g, "").trim();
      const parsed = JSON.parse(clean);
      setReport(parsed);
      setActiveTab("matrix");
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [rawInput]);

  const tabs = [
    { id: "matrix", label: "Priority Matrix" },
    { id: "workloads", label: `Workloads (${report?.workloads?.length ?? 0})` },
    { id: "patterns", label: "Patterns" },
    { id: "actions", label: "Actions" },
  ];

  return (
    <div style={{ background: COLORS.bg, minHeight: "100vh", color: COLORS.text, fontFamily: "Inter, system-ui, sans-serif", padding: 20 }}>
      {/* Header */}
      <div style={{ marginBottom: 20 }}>
        <div style={{ fontSize: 20, fontWeight: 700, color: COLORS.text }}>PerfectScale Batch Analyzer</div>
        <div style={{ fontSize: 13, color: COLORS.textMuted, marginTop: 2 }}>
          Paste an array of workload debug payloads to generate a consolidated coaching report.
        </div>
      </div>

      {/* Input */}
      {!report && (
        <div style={{ background: COLORS.card, border: `1px solid ${COLORS.cardBorder}`, borderRadius: 8, padding: 16, marginBottom: 20 }}>
          <div style={{ fontSize: 12, color: COLORS.textMuted, marginBottom: 8 }}>
            Paste a JSON array of PerfectScale debug payloads, or a single payload object:
          </div>
          <textarea
            value={rawInput}
            onChange={e => setRawInput(e.target.value)}
            placeholder={'[\n  { "recommendations": { ... }, ... },\n  { "recommendations": { ... }, ... }\n]'}
            style={{
              width: "100%",
              height: 160,
              background: "#0f172a",
              color: COLORS.text,
              border: `1px solid ${COLORS.cardBorder}`,
              borderRadius: 6,
              padding: 10,
              fontFamily: "monospace",
              fontSize: 12,
              resize: "vertical",
              boxSizing: "border-box",
            }}
          />
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 10 }}>
            <button
              onClick={analyze}
              disabled={loading || !rawInput.trim()}
              style={{
                background: loading || !rawInput.trim() ? COLORS.cardBorder : COLORS.accent,
                color: loading || !rawInput.trim() ? COLORS.textDim : "#fff",
                border: "none",
                borderRadius: 6,
                padding: "8px 20px",
                fontWeight: 700,
                fontSize: 13,
                cursor: loading || !rawInput.trim() ? "not-allowed" : "pointer",
              }}
            >
              {loading ? "Analyzing…" : "Analyze All Workloads"}
            </button>
            {error && <span style={{ color: COLORS.red, fontSize: 12 }}>Error: {error}</span>}
          </div>
        </div>
      )}

      {/* Report */}
      {report && (
        <>
          {/* Summary bar */}
          <div style={{
            background: COLORS.card,
            border: `1px solid ${COLORS.cardBorder}`,
            borderRadius: 8,
            padding: "12px 16px",
            marginBottom: 16,
            display: "flex",
            gap: 24,
            flexWrap: "wrap",
            alignItems: "center",
          }}>
            <div>
              <div style={{ fontSize: 11, color: COLORS.textDim }}>Workloads</div>
              <div style={{ fontWeight: 700, fontSize: 20, color: COLORS.text }}>{report.summary?.totalWorkloads ?? report.workloads?.length}</div>
            </div>
            <div>
              <div style={{ fontSize: 11, color: COLORS.textDim }}>Cluster</div>
              <div style={{ fontWeight: 600, fontSize: 14, color: COLORS.accentLight }}>{report.summary?.cluster ?? "—"}</div>
            </div>
            <div>
              <div style={{ fontSize: 11, color: COLORS.textDim }}>Customer</div>
              <div style={{ fontWeight: 600, fontSize: 14, color: COLORS.accentLight }}>{report.summary?.customer ?? "—"}</div>
            </div>
            <div>
              <div style={{ fontSize: 11, color: COLORS.textDim }}>Total Waste</div>
              <div style={{ fontWeight: 700, fontSize: 14, color: COLORS.yellow }}>{report.summary?.totalWaste ?? "—"}</div>
            </div>
            <div>
              <div style={{ fontSize: 11, color: COLORS.textDim }}>Blocked Risk Mit.</div>
              <div style={{ fontWeight: 700, fontSize: 14, color: COLORS.red }}>{report.summary?.totalBlockedRiskMitigation ?? "—"}</div>
            </div>
            <div style={{ flex: 1, minWidth: 200 }}>
              <div style={{ fontSize: 12, color: COLORS.textMuted, fontStyle: "italic" }}>{report.summary?.narrative}</div>
            </div>
            <button
              onClick={() => { setReport(null); setRawInput(""); }}
              style={{ background: "none", border: `1px solid ${COLORS.cardBorder}`, color: COLORS.textMuted, borderRadius: 5, padding: "4px 10px", fontSize: 12, cursor: "pointer" }}
            >New Analysis</button>
          </div>

          {/* Tabs */}
          <div style={{ display: "flex", gap: 4, marginBottom: 14 }}>
            {tabs.map(t => (
              <button
                key={t.id}
                onClick={() => setActiveTab(t.id)}
                style={{
                  background: activeTab === t.id ? COLORS.accent : COLORS.card,
                  color: activeTab === t.id ? "#fff" : COLORS.textMuted,
                  border: `1px solid ${activeTab === t.id ? COLORS.accent : COLORS.cardBorder}`,
                  borderRadius: 6,
                  padding: "6px 14px",
                  fontSize: 12,
                  fontWeight: activeTab === t.id ? 700 : 400,
                  cursor: "pointer",
                }}
              >{t.label}</button>
            ))}
          </div>

          {/* Priority Matrix */}
          {activeTab === "matrix" && (
            <div style={{ background: COLORS.card, border: `1px solid ${COLORS.cardBorder}`, borderRadius: 8, overflow: "hidden", marginBottom: 20 }}>
              <table style={{ width: "100%", borderCollapse: "collapse" }}>
                <thead>
                  <tr style={{ background: "#0f172a" }}>
                    {["#", "Workload", "Namespace", "Waste (30d)", "Risk Mit. Blocked", "Top Indicator", "Status"].map(h => (
                      <th key={h} style={{ padding: "8px 12px", textAlign: "left", fontSize: 11, color: COLORS.textDim, textTransform: "uppercase", letterSpacing: 1, fontWeight: 600, borderBottom: `1px solid ${COLORS.cardBorder}` }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {(report.workloads || []).map((w, i) => (
                    <tr key={i} style={{ borderBottom: `1px solid ${COLORS.cardBorder}22` }}>
                      <td style={{ padding: "8px 12px", color: COLORS.textDim, fontSize: 12 }}>{w.rank}</td>
                      <td style={{ padding: "8px 12px" }}>
                        <div style={{ fontWeight: 600, fontSize: 13, color: COLORS.text }}>{w.name}</div>
                        <div style={{ fontSize: 11, color: COLORS.textDim }}>{w.type}</div>
                      </td>
                      <td style={{ padding: "8px 12px", fontSize: 12, color: COLORS.accentLight }}>{w.namespace}</td>
                      <td style={{ padding: "8px 12px", fontWeight: 700, fontSize: 13, color: COLORS.yellow }}>{w.waste30d}</td>
                      <td style={{ padding: "8px 12px", fontWeight: 700, fontSize: 13, color: COLORS.red }}>{w.blockedRiskMitigation || "—"}</td>
                      <td style={{ padding: "8px 12px", fontSize: 12, color: COLORS.textMuted }}>{w.topIndicator}</td>
                      <td style={{ padding: "8px 12px" }}>
                        <Badge label={w.automationStatus} color={w.automationStatus?.includes("User") ? COLORS.orange : COLORS.yellow} small />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Workload Cards */}
          {activeTab === "workloads" && (
            <div>{(report.workloads || []).map((w, i) => <WorkloadCard key={i} w={w} index={i} />)}</div>
          )}

          {/* Patterns */}
          {activeTab === "patterns" && <PatternsPanel patterns={report.patterns} />}

          {/* Actions */}
          {activeTab === "actions" && <UnifiedActions actions={report.unifiedActions} />}
        </>
      )}
    </div>
  );
}
