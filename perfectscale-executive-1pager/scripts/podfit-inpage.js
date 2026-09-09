(async () => {
  const PERIOD = '30d';
  // Agent: replace these two arrays before eval-file.
  const CLUSTERS = [];
  const EXCLUDE = [];
  const token = sessionStorage.getItem('psc-impersonation-token') || localStorage.getItem('psc-token');
  if (!token) return { error: 'no token' };
  if (!CLUSTERS.length) return { error: 'set CLUSTERS = [{name, uid}, ...] at top of this script' };
  const excludeNeedles = EXCLUDE.map((s) => String(s).toLowerCase());

  const ELIGIBLE = new Set(['NotEnabled', 'DisabledByUser']);
  const r2 = (v) => Math.round((v || 0) * 100) / 100;
  const isExcluded = (w) => {
    if (!excludeNeedles.length) return false;
    const hay = `${w.name || ''} ${w.namespace || ''}`.toLowerCase();
    return excludeNeedles.some((s) => s && hay.includes(s));
  };
  const isNSFamily = (w) => {
    const n = `${w.name || ''} ${w.workloadType || ''}`.toLowerCase();
    if (/elastic|opensearch|kibana/.test(n)) return 'search';
    if (/kafka|strimzi/.test(n)) return 'kafka';
    return w.workloadType || 'other';
  };

  const results = {};
  for (const c of CLUSTERS) {
    const url = `https://api.app.perfectscale.io/psc-data-provider/api/v1/clusters/uid/${c.uid}/podfit/${PERIOD}?expand_ephemeral_workloads=false`;
    const resp = await fetch(url, { headers: { Authorization: 'Bearer ' + token } });
    const raw = await resp.json();
    if (!resp.ok) {
      results[c.name] = { error: resp.status };
      continue;
    }
    const all = raw.data.workloads || [];
    const excluded = all.filter(isExcluded);
    const workloads = all.filter((w) => !isExcluded(w));
    const statusCounts = {};
    let eligibleSav = 0, eligibleN = 0, nsSav = 0, nsN = 0, autoN = 0, limitedN = 0;
    const nsFamily = {};
    const eligTop = [];
    const nsTop = [];
    workloads.forEach((w) => {
      const s = w.workloadAutomationStatusV2 || '';
      statusCounts[s] = (statusCounts[s] || 0) + 1;
      const sav = w.wasteTimeFrameUsd || 0;
      if (ELIGIBLE.has(s) && w.namespace !== '__deleted-namespaces__') {
        eligibleN++; eligibleSav += sav;
        eligTop.push({ name: w.name, ns: w.namespace, type: w.workloadType, sav: r2(sav), status: s });
      }
      if (s === 'NotSupported' && w.namespace !== '__deleted-namespaces__') {
        nsN++; nsSav += sav;
        const fam = isNSFamily(w);
        if (!nsFamily[fam]) nsFamily[fam] = { n: 0, sav: 0 };
        nsFamily[fam].n++; nsFamily[fam].sav += sav;
        nsTop.push({ name: w.name, ns: w.namespace, type: w.workloadType, sav: r2(sav), fam });
      }
      if (s === 'Automated' || s === 'EnabledAutomated') autoN++;
      if (s === 'Limited' || s === 'AutomatedLimited' || s === 'AutomatedPartially') limitedN++;
    });
    eligTop.sort((a, b) => b.sav - a.sav);
    nsTop.sort((a, b) => b.sav - a.sav);
    Object.keys(nsFamily).forEach((k) => { nsFamily[k].sav = r2(nsFamily[k].sav); });
    results[c.name] = {
      totalWorkloads: workloads.length,
      totalWorkloadsBeforeExclusion: all.length,
      excluded: {
        n: excluded.length,
        sav: r2(excluded.reduce((s, w) => s + (w.wasteTimeFrameUsd || 0), 0)),
        names: [...new Set(excluded.map((w) => w.name).filter(Boolean))],
      },
      statusCounts,
      automated: autoN,
      limited: limitedN,
      eligible: { n: eligibleN, sav: r2(eligibleSav), top: eligTop.slice(0, 5) },
      notSupported: { n: nsN, sav: r2(nsSav), family: nsFamily, top: nsTop.slice(0, 5) },
      apiSummary: raw.data.summary || {},
    };
  }
  return results;
})()
