import { useState, useCallback, useRef } from "react";

const SYSTEM_PROMPT = `You are a PerfectScale technical coaching expert helping internal teams analyze workload debug payloads to coach customers.

You will receive a PerfectScale zoom-in debug JSON payload. Analyze it thoroughly and produce a structured coaching report.

Respond ONLY with valid JSON (no markdown, no backticks, no preamble) in this exact shape:
{
  "workload": {
    "name": "string",
    "namespace": "string",
    "type": "string",
    "cluster": "string (infer from filename/context if present)",
    "customer": "string (infer if present)",
    "dataWindow": "string (e.g. '90 days, 4 revisions')",
    "automationStatus": "string",
    "optimizationPolicy": "string",
    "savingsPct": "string",
    "totalCost": "string",
    "estimatedCost": "string",
    "maturity": "boolean"
  },
  "containers": [
    {
      "name": "string",
      "primaryStory": "waste | risk | mixed",
      "headline": "string (one sharp sentence summarizing the key finding)",
      "indicators": [
        { "name": "string", "type": "waste | risk", "severity": "number" }
      ],
      "currentResources": {
        "cpuRequest": "string", "cpuLimit": "string",
        "memRequest": "string", "memLimit": "string"
      },
      "recommendedResources": {
        "cpuRequest": "string", "cpuLimit": "string",
        "memRequest": "string", "memLimit": "string"
      },
      "actualUsage": {
        "cpuAvg": "string", "cpuP99": "string", "cpuMax": "string",
        "memAvg": "string", "memP99": "string", "memMax": "string",
        "throttlePct": "string"
      },
      "monthlyWaste": "string",
      "reasoningChain": {
        "memory": ["string"],
        "cpu": ["string"],
        "keyInsights": ["string (plain-English explanation of notable steps in the chain)"]
      },
      "coachingPoints": ["string (customer-ready talking points)"],
      "safeToApply": "boolean",
      "cautions": ["string"]
    }
  ],
  "workloadLevelFindings": ["string"],
  "automationBlockers": ["string (reasons automation isn't applied, if any)"],
  "revisionHistory": [
    { "id": "string", "hash": "string", "firstSeen": "string", "lastSeen": "string", "notes": "string" }
  ],
  "coachingNarrative": "string (3-5 sentence customer-ready summary a coach can read verbatim)",
  "recommendedActions": ["string (ordered list of concrete next steps)"],
  "riskFlags": ["string (anything that warrants caution before applying)"]
}`;

const formatCurrency = (val) => {
  const n = parseFloat(val);
  if (isNaN(n)) return val;
  return `$${n.toFixed(2)}`;
};

const severityColor = (s) => {
  if (s >= 3) return "#ef4444";
  if (s >= 2) return "#f97316";
  return "#eab308";
};

const typeColor = (t) => (t === "risk" ? "#ef4444" : "#f97316");

const Badge = ({ label, color, bg }) => (
  <span style={{
    display: "inline-flex", alignItems: "center", gap: 4,
    padding: "2px 8px", borderRadius: 999, fontSize: 11, fontWeight: 600,
    background: bg || color + "22", color: color, border: `1px solid ${color}44`,
    whiteSpace: "nowrap"
  }}>{label}</span>
);

const Section = ({ title, children, accent }) => (
  <div style={{ marginBottom: 24 }}>
    <div style={{
      display: "flex", alignItems: "center", gap: 8, marginBottom: 12,
      borderBottom: `2px solid ${accent || "#334155"}`, paddingBottom: 6
    }}>
      <span style={{ fontSize: 11, fontWeight: 700, letterSpacing: "0.08em",
        textTransform: "uppercase", color: accent || "#94a3b8" }}>{title}</span>
    </div>
    {children}
  </div>
);

const ResourceTable = ({ current, recommended }) => {
  const rows = [
    ["CPU Request", current.cpuRequest, recommended.cpuRequest],
    ["CPU Limit", current.cpuLimit, recommended.cpuLimit],
    ["Mem Request", current.memRequest, recommended.memRequest],
    ["Mem Limit", current.memLimit, recommended.memLimit],
  ];
  return (
    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
      <thead>
        <tr>
          {["", "Current", "Recommended"].map(h => (
            <th key={h} style={{ textAlign: h === "" ? "left" : "center",
              padding: "4px 8px", color: "#64748b", fontWeight: 600,
              borderBottom: "1px solid #1e293b" }}>{h}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map(([label, cur, rec]) => {
          const changed = cur !== rec;
          return (
            <tr key={label} style={{ background: changed ? "#0f172a" : "transparent" }}>
              <td style={{ padding: "5px 8px", color: "#94a3b8", fontWeight: 500 }}>{label}</td>
              <td style={{ padding: "5px 8px", textAlign: "center", color: changed ? "#f87171" : "#64748b",
                fontFamily: "monospace" }}>{cur}</td>
              <td style={{ padding: "5px 8px", textAlign: "center", color: changed ? "#4ade80" : "#64748b",
                fontFamily: "monospace", fontWeight: changed ? 700 : 400 }}>{rec}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
};

const ContainerCard = ({ container }) => {
  const [expanded, setExpanded] = useState(false);
  const accentColor = container.primaryStory === "risk" ? "#ef4444"
    : container.primaryStory === "mixed" ? "#f97316" : "#f59e0b";

  return (
    <div style={{
      background: "#0f172a", borderRadius: 10, border: `1px solid ${accentColor}33`,
      overflow: "hidden", marginBottom: 16
    }}>
      <div style={{
        padding: "14px 16px", cursor: "pointer",
        borderLeft: `3px solid ${accentColor}`,
        display: "flex", justifyContent: "space-between", alignItems: "flex-start"
      }} onClick={() => setExpanded(e => !e)}>
        <div style={{ flex: 1 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", marginBottom: 6 }}>
            <span style={{ fontWeight: 700, fontSize: 14, color: "#f1f5f9" }}>{container.name}</span>
            {container.indicators?.map(ind => (
              <Badge key={ind.name} label={ind.name}
                color={typeColor(ind.type)}
                bg={typeColor(ind.type) + "15"} />
            ))}
            {container.monthlyWaste && parseFloat(container.monthlyWaste) > 0 && (
              <Badge label={`~${formatCurrency(container.monthlyWaste)}/mo waste`} color="#f59e0b" />
            )}
          </div>
          <p style={{ margin: 0, fontSize: 12, color: "#94a3b8", lineHeight: 1.5 }}>{container.headline}</p>
        </div>
        <span style={{ color: "#475569", fontSize: 16, marginLeft: 12, marginTop: 2 }}>
          {expanded ? "▲" : "▼"}
        </span>
      </div>

      {expanded && (
        <div style={{ padding: "16px", borderTop: `1px solid #1e293b` }}>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginBottom: 20 }}>
            <div>
              <Section title="Resource Changes" accent={accentColor}>
                <ResourceTable current={container.currentResources} recommended={container.recommendedResources} />
              </Section>
            </div>
            <div>
              <Section title="Actual Usage" accent="#334155">
                <table style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
                  <tbody>
                    {[
                      ["CPU avg", container.actualUsage?.cpuAvg],
                      ["CPU p99", container.actualUsage?.cpuP99],
                      ["CPU max", container.actualUsage?.cpuMax],
                      ["Mem avg", container.actualUsage?.memAvg],
                      ["Mem p99", container.actualUsage?.memP99],
                      ["Mem max", container.actualUsage?.memMax],
                      ["Throttle", container.actualUsage?.throttlePct],
                    ].filter(([, v]) => v && v !== "0%" && v !== "0").map(([label, val]) => (
                      <tr key={label}>
                        <td style={{ padding: "3px 0", color: "#64748b", width: "50%" }}>{label}</td>
                        <td style={{ padding: "3px 0", color: "#cbd5e1", fontFamily: "monospace" }}>{val}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Section>
            </div>
          </div>

          {container.reasoningChain?.keyInsights?.length > 0 && (
            <Section title="Why PerfectScale proposed this" accent="#7c3aed">
              <ul style={{ margin: 0, padding: "0 0 0 16px" }}>
                {container.reasoningChain.keyInsights.map((insight, i) => (
                  <li key={i} style={{ fontSize: 12, color: "#94a3b8", marginBottom: 6, lineHeight: 1.6 }}>{insight}</li>
                ))}
              </ul>
            </Section>
          )}

          {container.coachingPoints?.length > 0 && (
            <Section title="Coaching Talking Points" accent="#0ea5e9">
              <ul style={{ margin: 0, padding: "0 0 0 16px" }}>
                {container.coachingPoints.map((pt, i) => (
                  <li key={i} style={{ fontSize: 12, color: "#bfdbfe", marginBottom: 6, lineHeight: 1.6 }}>{pt}</li>
                ))}
              </ul>
            </Section>
          )}

          {container.cautions?.length > 0 && (
            <div style={{
              background: "#1c0a0a", border: "1px solid #7f1d1d", borderRadius: 6,
              padding: "10px 14px", marginTop: 8
            }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: "#fca5a5",
                textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 6 }}>⚠ Cautions</div>
              {container.cautions.map((c, i) => (
                <p key={i} style={{ margin: "4px 0", fontSize: 12, color: "#fca5a5" }}>{c}</p>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default function App() {
  const [mode, setMode] = useState("paste"); // paste | upload
  const [input, setInput] = useState("");
  const [fileName, setFileName] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);
  const fileRef = useRef();

  const analyze = useCallback(async (jsonText) => {
    setLoading(true);
    setError(null);
    setResult(null);

    try {
      JSON.parse(jsonText); // validate JSON first
    } catch {
      setError("Invalid JSON — please check the payload and try again.");
      setLoading(false);
      return;
    }

    try {
      let rawText;

      if (window.cowork?.askClaude) {
        rawText = await window.cowork.askClaude(
          `${SYSTEM_PROMPT}\n\nAnalyze this PerfectScale debug payload and respond with valid JSON only:\n\n${jsonText}`
        );
      } else {
        const response = await fetch("https://api.anthropic.com/v1/messages", {
          method: "POST",
          headers: { "Content-Type": "application/json", "anthropic-version": "2023-06-01" },
          body: JSON.stringify({
            model: "claude-sonnet-4-6",
            max_tokens: 8000,
            system: SYSTEM_PROMPT,
            messages: [{ role: "user", content: `Analyze this PerfectScale debug payload:\n\n${jsonText}` }]
          })
        });
        const data = await response.json();
        rawText = data.content?.find(b => b.type === "text")?.text || "";
      }

      let clean = (rawText || "").replace(/```json|```/g, "").trim();

      let parsed;
      try {
        parsed = JSON.parse(clean);
      } catch {
        // Response was likely truncated — trim to last safe close point
        const safeEnd = Math.max(
          clean.lastIndexOf("},"),
          clean.lastIndexOf("},\n"),
          clean.lastIndexOf("]},"),
          clean.lastIndexOf("]\n}")
        );
        if (safeEnd > 0) clean = clean.slice(0, safeEnd + 1);
        // Close any open braces/brackets
        let obj = 0, arr = 0;
        for (const ch of clean) {
          if (ch === "{") obj++;
          else if (ch === "}") obj--;
          else if (ch === "[") arr++;
          else if (ch === "]") arr--;
        }
        for (let i = 0; i < arr; i++) clean += "]";
        for (let i = 0; i < obj; i++) clean += "}";
        parsed = JSON.parse(clean);
        parsed._truncated = true;
      }
      setResult(parsed);
    } catch (err) {
      setError("Analysis failed — " + err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  const handleFile = (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setFileName(file.name);
    const reader = new FileReader();
    reader.onload = (ev) => {
      const text = ev.target.result;
      setInput(text);
      analyze(text);
    };
    reader.readAsText(file);
  };

  const handleDrop = (e) => {
    e.preventDefault();
    const file = e.dataTransfer.files?.[0];
    if (!file) return;
    setFileName(file.name);
    const reader = new FileReader();
    reader.onload = (ev) => {
      setInput(ev.target.result);
      analyze(ev.target.result);
    };
    reader.readAsText(file);
  };

  const handlePasteAnalyze = () => {
    if (!input.trim()) return;
    analyze(input);
  };

  const reset = () => {
    setResult(null); setError(null); setInput(""); setFileName(null);
    if (fileRef.current) fileRef.current.value = "";
  };

  return (
    <div style={{
      minHeight: "100vh", background: "#020817",
      fontFamily: "'Inter', system-ui, -apple-system, sans-serif",
      color: "#f1f5f9", padding: "0 0 48px"
    }}>

      {/* Header */}
      <div style={{
        borderBottom: "1px solid #1e293b", padding: "18px 32px",
        display: "flex", alignItems: "center", justifyContent: "space-between"
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <div style={{
            width: 32, height: 32, borderRadius: 8,
            background: "linear-gradient(135deg, #6366f1, #8b5cf6)",
            display: "flex", alignItems: "center", justifyContent: "center",
            fontSize: 16
          }}>⚡</div>
          <div>
            <div style={{ fontSize: 15, fontWeight: 700, color: "#f1f5f9" }}>PerfectScale Debug Analyzer</div>
            <div style={{ fontSize: 11, color: "#475569" }}>Internal coaching tool — workload zoom-in payload</div>
          </div>
        </div>
        {result && (
          <button onClick={reset} style={{
            background: "transparent", border: "1px solid #334155",
            color: "#94a3b8", padding: "6px 14px", borderRadius: 6,
            cursor: "pointer", fontSize: 12, fontWeight: 600
          }}>← New Analysis</button>
        )}
      </div>

      <div style={{ maxWidth: 900, margin: "0 auto", padding: "32px 24px" }}>

        {/* Input area — only shown when no result yet */}
        {!result && !loading && (
          <div>
            {/* Mode toggle */}
            <div style={{ display: "flex", gap: 8, marginBottom: 24 }}>
              {["paste", "upload"].map(m => (
                <button key={m} onClick={() => { setMode(m); setError(null); }} style={{
                  padding: "8px 20px", borderRadius: 6, fontSize: 13, fontWeight: 600,
                  cursor: "pointer", border: "none",
                  background: mode === m ? "#6366f1" : "#0f172a",
                  color: mode === m ? "#fff" : "#64748b",
                  transition: "all 0.15s"
                }}>
                  {m === "paste" ? "📋 Paste JSON" : "📁 Upload File"}
                </button>
              ))}
            </div>

            {mode === "paste" && (
              <div>
                <textarea
                  value={input}
                  onChange={e => setInput(e.target.value)}
                  placeholder={`Paste PerfectScale zoom-in debug JSON here...\n\n{\n  "recommendations": { ... },\n  "revisions": [ ... ],\n  "mergedRevision": { ... }\n}`}
                  style={{
                    width: "100%", minHeight: 260, background: "#0f172a",
                    border: "1px solid #1e293b", borderRadius: 8,
                    color: "#cbd5e1", fontFamily: "monospace", fontSize: 12,
                    padding: 16, resize: "vertical", outline: "none",
                    boxSizing: "border-box",
                    lineHeight: 1.6
                  }}
                />
                <button
                  onClick={handlePasteAnalyze}
                  disabled={!input.trim()}
                  style={{
                    marginTop: 12, padding: "10px 28px", borderRadius: 7,
                    background: input.trim() ? "linear-gradient(135deg, #6366f1, #8b5cf6)" : "#1e293b",
                    color: input.trim() ? "#fff" : "#475569",
                    border: "none", cursor: input.trim() ? "pointer" : "default",
                    fontWeight: 700, fontSize: 13, transition: "all 0.15s"
                  }}>
                  Analyze Payload →
                </button>
              </div>
            )}

            {mode === "upload" && (
              <div
                onDrop={handleDrop}
                onDragOver={e => e.preventDefault()}
                onClick={() => fileRef.current?.click()}
                style={{
                  border: "2px dashed #334155", borderRadius: 10,
                  padding: "60px 32px", textAlign: "center",
                  cursor: "pointer", transition: "border-color 0.15s",
                  background: "#0a1020"
                }}
                onMouseEnter={e => e.currentTarget.style.borderColor = "#6366f1"}
                onMouseLeave={e => e.currentTarget.style.borderColor = "#334155"}
              >
                <div style={{ fontSize: 36, marginBottom: 12 }}>📂</div>
                <div style={{ fontSize: 14, fontWeight: 600, color: "#e2e8f0", marginBottom: 6 }}>
                  Drop your JSON file here, or click to browse
                </div>
                <div style={{ fontSize: 12, color: "#475569" }}>
                  Accepts .json files exported from the PerfectScale zoom-in debug window
                </div>
                {fileName && (
                  <div style={{
                    marginTop: 16, display: "inline-flex", alignItems: "center", gap: 6,
                    background: "#1e293b", borderRadius: 6, padding: "6px 12px",
                    fontSize: 12, color: "#94a3b8"
                  }}>
                    📄 {fileName}
                  </div>
                )}
                <input
                  ref={fileRef}
                  type="file"
                  accept=".json"
                  style={{ display: "none" }}
                  onChange={handleFile}
                />
              </div>
            )}

            {error && (
              <div style={{
                marginTop: 16, background: "#1c0a0a", border: "1px solid #7f1d1d",
                borderRadius: 8, padding: "12px 16px", color: "#fca5a5", fontSize: 13
              }}>
                ⚠ {error}
              </div>
            )}
          </div>
        )}

        {/* Loading state */}
        {loading && (
          <div style={{ textAlign: "center", padding: "80px 0" }}>
            <div style={{ fontSize: 36, marginBottom: 16, animation: "spin 1.5s linear infinite",
              display: "inline-block" }}>⚡</div>
            <div style={{ fontSize: 14, color: "#94a3b8", marginBottom: 6 }}>Analyzing workload...</div>
            <div style={{ fontSize: 12, color: "#475569" }}>
              Reading reasoning chains, usage telemetry, and automation state
            </div>
            <style>{`@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }`}</style>
          </div>
        )}

        {/* Results */}
        {result && (
          <div>
            {result._truncated && (
          <div style={{
            background: "#1c1000", border: "1px solid #92400e", borderRadius: 8,
            padding: "10px 16px", marginBottom: 16, fontSize: 12, color: "#fcd34d",
            display: "flex", alignItems: "center", gap: 8
          }}>
            <span>⚠</span>
            <span>The API response was truncated and partially recovered — some container details near the end may be incomplete. Consider re-running the analysis.</span>
          </div>
        )}

        {/* Workload header */}
            <div style={{
              background: "#0f172a", borderRadius: 12, border: "1px solid #1e293b",
              padding: 24, marginBottom: 24
            }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: 12 }}>
                <div>
                  <div style={{ fontSize: 20, fontWeight: 800, color: "#f1f5f9", marginBottom: 4 }}>
                    {result.workload?.name}
                  </div>
                  <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                    {result.workload?.namespace && <Badge label={`ns: ${result.workload.namespace}`} color="#64748b" />}
                    {result.workload?.type && <Badge label={result.workload.type} color="#6366f1" />}
                    {result.workload?.customer && <Badge label={result.workload.customer} color="#0ea5e9" />}
                    {result.workload?.maturity && <Badge label="Mature ✓" color="#22c55e" />}
                    <Badge
                      label={result.workload?.automationStatus || "Automation Unknown"}
                      color={result.workload?.automationStatus?.toLowerCase().includes("disabled") ? "#ef4444" : "#22c55e"}
                    />
                  </div>
                </div>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 16, textAlign: "center" }}>
                  {[
                    ["Current Cost", result.workload?.totalCost ? formatCurrency(result.workload.totalCost) : "—"],
                    ["Projected Cost", result.workload?.estimatedCost ? formatCurrency(result.workload.estimatedCost) : "—"],
                    ["Savings", result.workload?.savingsPct ? `${parseFloat(result.workload.savingsPct).toFixed(1)}%` : "—"],
                  ].map(([label, val]) => (
                    <div key={label}>
                      <div style={{ fontSize: 18, fontWeight: 800, color: "#f1f5f9" }}>{val}</div>
                      <div style={{ fontSize: 10, color: "#475569", textTransform: "uppercase", letterSpacing: "0.06em" }}>{label}</div>
                    </div>
                  ))}
                </div>
              </div>

              <div style={{ marginTop: 16, padding: 14, background: "#0a1020", borderRadius: 8,
                borderLeft: "3px solid #6366f1" }}>
                <div style={{ fontSize: 11, fontWeight: 700, color: "#818cf8",
                  textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 6 }}>Coaching Narrative</div>
                <p style={{ margin: 0, fontSize: 13, color: "#cbd5e1", lineHeight: 1.7 }}>
                  {result.coachingNarrative}
                </p>
              </div>
            </div>

            {/* Containers */}
            <Section title={`Containers (${result.containers?.length || 0})`} accent="#6366f1">
              {result.containers?.map(c => <ContainerCard key={c.name} container={c} />)}
            </Section>

            {/* Recommended Actions */}
            {result.recommendedActions?.length > 0 && (
              <Section title="Recommended Actions" accent="#22c55e">
                <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                  {result.recommendedActions.map((action, i) => (
                    <div key={i} style={{
                      display: "flex", gap: 12, alignItems: "flex-start",
                      background: "#0f172a", borderRadius: 8, padding: "10px 14px",
                      border: "1px solid #1e293b"
                    }}>
                      <span style={{
                        width: 22, height: 22, borderRadius: "50%",
                        background: "#14532d", color: "#4ade80",
                        display: "flex", alignItems: "center", justifyContent: "center",
                        fontSize: 11, fontWeight: 800, flexShrink: 0, marginTop: 1
                      }}>{i + 1}</span>
                      <span style={{ fontSize: 13, color: "#cbd5e1", lineHeight: 1.6 }}>{action}</span>
                    </div>
                  ))}
                </div>
              </Section>
            )}

            {/* Risk Flags + Automation Blockers side by side */}
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
              {result.riskFlags?.length > 0 && (
                <Section title="Risk Flags" accent="#ef4444">
                  {result.riskFlags.map((r, i) => (
                    <div key={i} style={{
                      background: "#1c0a0a", borderRadius: 6, padding: "8px 12px",
                      marginBottom: 6, fontSize: 12, color: "#fca5a5", lineHeight: 1.6,
                      borderLeft: "2px solid #ef4444"
                    }}>⚠ {r}</div>
                  ))}
                </Section>
              )}
              {result.automationBlockers?.length > 0 && (
                <Section title="Automation Blockers" accent="#f59e0b">
                  {result.automationBlockers.map((b, i) => (
                    <div key={i} style={{
                      background: "#1c1000", borderRadius: 6, padding: "8px 12px",
                      marginBottom: 6, fontSize: 12, color: "#fcd34d", lineHeight: 1.6,
                      borderLeft: "2px solid #f59e0b"
                    }}>⚡ {b}</div>
                  ))}
                </Section>
              )}
            </div>

            {/* Revision History */}
            {result.revisionHistory?.length > 0 && (
              <Section title="Revision History" accent="#334155">
                <table style={{ width: "100%", fontSize: 11, borderCollapse: "collapse" }}>
                  <thead>
                    <tr>
                      {["ID", "Hash", "First Seen", "Last Seen", "Notes"].map(h => (
                        <th key={h} style={{ textAlign: "left", padding: "4px 8px",
                          color: "#475569", fontWeight: 600, borderBottom: "1px solid #1e293b" }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {result.revisionHistory.map((r, i) => (
                      <tr key={i} style={{ background: i === 0 ? "#0a1020" : "transparent" }}>
                        <td style={{ padding: "5px 8px", fontFamily: "monospace", color: i === 0 ? "#818cf8" : "#64748b" }}>{r.id}</td>
                        <td style={{ padding: "5px 8px", fontFamily: "monospace", color: "#475569", fontSize: 10 }}>{r.hash}</td>
                        <td style={{ padding: "5px 8px", color: "#64748b" }}>{r.firstSeen}</td>
                        <td style={{ padding: "5px 8px", color: "#64748b" }}>{r.lastSeen}</td>
                        <td style={{ padding: "5px 8px", color: "#94a3b8" }}>{r.notes}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Section>
            )}

            {/* Workload-level findings */}
            {result.workloadLevelFindings?.length > 0 && (
              <Section title="Workload-Level Findings" accent="#334155">
                <ul style={{ margin: 0, padding: "0 0 0 16px" }}>
                  {result.workloadLevelFindings.map((f, i) => (
                    <li key={i} style={{ fontSize: 12, color: "#94a3b8", marginBottom: 6, lineHeight: 1.6 }}>{f}</li>
                  ))}
                </ul>
              </Section>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
