(() => {
  const money = (s) => {
    if (!s) return null;
    const t = String(s).replace(/,/g, '').trim();
    const m = t.match(/\$?\s*([0-9]+(?:\.[0-9]+)?)\s*([KkMm])?/);
    if (!m) return t;
    let n = parseFloat(m[1]);
    if (m[2] === 'K' || m[2] === 'k') n *= 1000;
    if (m[2] === 'M' || m[2] === 'm') n *= 1e6;
    return Math.round(n);
  };
  const pick = (text, re) => {
    const m = text.match(re);
    return m ? m[1].trim() : null;
  };
  const text = document.body.innerText || '';
  const tenant = {
    highRisks: pick(text, /Current High Risks\n([0-9,]+)/),
    podWaste: pick(text, /Pod Waste\n(\$[0-9,.]+[KkMm]?)/),
    nodeIdle: pick(text, /Node Idle\n(\$[0-9,.]+[KkMm]?)/),
    cost: pick(text, /Cost \/ 30d\n(\$[0-9,.]+[KkMm]?)/),
    potentialPct: pick(text, /Potential Max Savings\n([0-9]+%)/),
    potentialMonthly: pick(text, /Monthly\n(\$[0-9,.]+[KkMm]?)/),
    potentialAnnual: pick(text, /Annually\n(\$[0-9,.]+[KkMm]?)/),
    optimizedCost: pick(text, /Potential Optimized Cost\n(\$[0-9,.]+[KkMm]?)/),
    saved: pick(text, /Total Saved\n(\$[0-9,.]+)/),
    risksAddressed: pick(text, /Total Risks Addressed\n([0-9,]+)/),
    impactDays: pick(text, /Impact Tracker\n([0-9]+)d/),
    impersonating: /IMPERSONATING/i.test(text),
    tenantHint: pick(text, /View More Details\n([^\n]+)/),
  };
  tenant.podWasteUsd = money(tenant.podWaste);
  tenant.nodeIdleUsd = money(tenant.nodeIdle);
  tenant.costUsd = money(tenant.cost);
  tenant.potentialMaxUsd = money(tenant.potentialMonthly);

  const known = []; // Agent: fill with cluster names from Overview / clusters list, e.g. ['prod-eu-west-a', ...]
  const lines = text.split('\n').map((l) => l.trim()).filter(Boolean);
  const clusters = [];
  for (let i = 0; i < lines.length; i++) {
    const name = lines[i];
    if (!known.length) continue;
    if (!known.includes(name)) continue;
    const slice = lines.slice(i, i + 20);
    const dollars = slice.filter((l) => /^\$/.test(l));
    const policy = slice.find((l) => /ExtraHeadroom|Balanced|MaxPerformance|MinCost/i.test(l)) || '';
    clusters.push({
      name,
      policy,
      potentialMax: dollars[0] || null,
      cost: dollars[1] || null,
      podWaste: dollars[2] || null,
      nodeIdle: dollars[3] || null,
      potentialMaxUsd: money(dollars[0]),
      costUsd: money(dollars[1]),
      podWasteUsd: money(dollars[2]),
      nodeIdleUsd: money(dollars[3]),
    });
  }
  clusters.sort((a, b) => (b.potentialMaxUsd || 0) - (a.potentialMaxUsd || 0));
  return {
    href: location.href,
    tenant,
    clusters,
    note: known.length
      ? 'Sorted by Potential Max Savings (parsed from Overview dollars).'
      : 'Fill the known = [] array at the top with cluster names, then re-run.',
  };
})()
