#!/usr/bin/env node

import fs from "node:fs";
import { generateTagAnalysis } from "../vendor/legal-opinion-tagger/tag-engine.mjs";

const SOURCE_MAP = {
  "제목": "title",
  "신청": "summary",
  "결과": "result",
  "신청부서의견": "body",
};

const AMBIGUITY_GUARDS = {
  "공제": /(보험.{0,12}공제|공제.{0,12}보험|공제회|공제조합|공제계약|공제금)/,
  "동의": /(개인정보.{0,16}동의|정보주체.{0,16}동의|수집.{0,12}동의|제공.{0,12}동의|마케팅.{0,12}동의)/,
};

function mapSource(source) {
  return SOURCE_MAP[source] || source;
}

function normalizeTag(tag, fullText) {
  const guard = AMBIGUITY_GUARDS[tag.label];
  const ambiguous = Boolean(guard && !guard.test(fullText));
  return {
    ...tag,
    sources: (tag.sources || []).map(mapSource),
    evidence: (tag.evidence || []).map((entry) => ({
      ...entry,
      source: mapSource(entry.source),
    })),
    candidateStatus: ambiguous ? "다의어 검토" : (tag.status || "직접 후보"),
    strictMatch: tag.score >= 88 && tag.status !== "연관 후보" && !ambiguous,
  };
}

function analyze(item) {
  const title = String(item.title || "").trim();
  const summary = String(item.summary || "").trim();
  const body = String(item.body || "").trim();
  const analysis = generateTagAnalysis(title, summary, "", body);
  const fullText = `${title}\n${summary}\n${body}`;
  const tags = analysis.normalizedTags.map((tag) => normalizeTag(tag, fullText));
  const byHashtag = new Map(tags.map((tag) => [tag.hashtag, tag]));
  const relations = (analysis.relations || []).map((relation) => ({
    ...relation,
    sourceId: byHashtag.get(relation.sourceTag)?.id || null,
    targetId: byHashtag.get(relation.targetTag)?.id || null,
  }));
  return { itemId: String(item.id), tags, relations };
}

try {
  const raw = fs.readFileSync(0, "utf8");
  const payload = JSON.parse(raw || "{}");
  const items = Array.isArray(payload.items) ? payload.items : [];
  process.stdout.write(JSON.stringify({ results: items.map(analyze) }));
} catch (error) {
  process.stderr.write(`${error?.stack || error}\n`);
  process.exitCode = 1;
}
