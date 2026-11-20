(async () => {
  const PERIOD = '30d';
  // Agent: replace these two arrays before eval-file.
  const CLUSTERS = [];
  const EXCLUDE = [];
  // Optional HPA-triggers capture (see references/eligibility-enrichment.md). Defaults off: the
  // endpoint/shape below is an UNCONFIRMED STUB (see captureHpaForWorkload below), not verified
  // against a real tenant. Flip this on only after an agent with real browser access has confirmed
  // the actual request the zoom-in modal's HPA Triggers section makes (Network tab) and filled in
  // HPA_ENDPOINT/the field names in captureHpaForWorkload.
  const CAPTURE_HPA = false;
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

  // STUB - not wired to a confirmed endpoint. The HPA Triggers view (per-container CPU/Memory HPA
  // trigger detection, replica scaling history) lives only in PodFit's workload zoom-in window; pscli
  // has no HPA field anywhere (confirmed via `pscli workloads -V policy/capacity/risk/all --help`),
  // the same "browser-only, no public-API equivalent" situation podfit-inpage.js already solves for
  // eligibility above. Unlike eligibility, the bulk `podfit/{PERIOD}` response above is not known to
  // carry HPA fields per workload - the zoom-in modal likely issues its own per-workload request when
  // opened, which this stub has not been run against a real tenant to confirm.
  //
  // TODO for an agent with real browser + tenant access, same shape as the eligibility loop below:
  //   1. Open a workload's zoom-in window in the PodFit UI with its HPA Triggers section visible.
  //   2. In DevTools Network, find the request that section's data actually comes from - confirm
  //      whether it is already present on the bulk podfit response (cheap: no extra per-workload
  //      calls needed) or a dedicated per-workload endpoint (expensive: one call per workload, so
  //      gate it further, e.g. only for the top-N by cost, the way eligTop/nsTop are already capped).
  //   3. Replace HPA_ENDPOINT and the field names in captureHpaTriggers below with the confirmed ones.
  //   4. Flip CAPTURE_HPA to true at the top of this file.
  const HPA_ENDPOINT = (uid, workloadId) =>
    `https://api.app.perfectscale.io/psc-data-provider/api/v1/clusters/uid/${uid}/workloads/${workloadId}/hpa`; // UNCONFIRMED path
  const captureHpaTriggers = async (uid, w) => {
    try {
      const resp = await fetch(HPA_ENDPOINT(uid, w.id), { headers: { Authorization: 'Bearer ' + token } });
      if (!resp.ok) return null;
      const d = await resp.json();
      // UNCONFIRMED field names - placeholder shape only, matching what the caller below expects.
      if (!d || !d.hasHpa) return null;
      return { hasHpa: true, cpuTriggerPct: d.cpuTriggerPct ?? null, memTriggerPct: d.memTriggerPct ?? null };
    } catch (e) {
      return null; // a wrong guess here must never break the eligibility pass above
    }
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

    // Optional, off by default (see captureHpaTriggers above - unconfirmed endpoint). When turned on,
    // builds exactly the {workloadId: {...}} shape hpa_file expects for this cluster's entry, the same
    // per-workload-id map shape eligibility_file expects for elig_map.
    let hpaById = null;
    if (CAPTURE_HPA) {
      hpaById = {};
      for (const w of workloads) {
        const hit = await captureHpaTriggers(c.uid, w);
        if (hit) hpaById[w.id] = hit;
      }
    }

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
      ...(hpaById ? { hpa: hpaById } : {}),
    };
  }
  return results;
})()
