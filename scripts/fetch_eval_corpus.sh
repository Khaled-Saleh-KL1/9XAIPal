#!/usr/bin/env bash
# Fetch the n=20 evaluation corpus for docs/plans/mineru-heuristic-removal.md §4.
#
# Corpus is weighted toward what the two original fixtures did NOT test:
# two-column layout, heavy LaTeX macros, and older/degraded typography.
# Layout labels below are EXPECTED, not verified — analyze_content_list.py
# determines actual layout from bbox clustering and is the authority.
#
# Polite to arXiv: sequential, 2s delay, skips files already present.
set -u
cd "$(dirname "$0")/.." || exit 1
DEST=samples/eval
mkdir -p "$DEST"

# id|filename|expected-class
PAPERS='
1512.00567|inception-v3|two-column
1608.06993|densenet|two-column
1703.06870|mask-rcnn|two-column
1611.05431|resnext|two-column
1801.04381|mobilenet-v2|two-column
1707.01083|shufflenet|two-column
1804.02767|yolov3|two-column
1409.4842|googlenet|two-column
1512.03385|resnet|two-column
2010.11929|vit|single-column
1706.03762|attention|single-column
hep-th/9711200|maldacena-adscft|macro-heavy
hep-th/9802150|witten-ads|macro-heavy
math/0211159|perelman-entropy|macro-heavy
gr-qc/9310026|old-1993-gr|old-typography
hep-th/9409089|old-1994-strings|old-typography
cond-mat/9707253|old-1997-condmat|old-typography
astro-ph/9805201|riess-1998-supernova|journal-style
q-bio/0512013|old-qbio|old-typography
cs/0011047|old-2000-cs|old-typography
'

n=0; got=0; failed=""
while IFS='|' read -r id name klass; do
  [ -z "${id:-}" ] && continue
  n=$((n+1))
  out="$DEST/${name}.pdf"
  if [ -s "$out" ]; then echo "  = $name (cached)"; got=$((got+1)); continue; fi
  curl -sL --max-time 120 -o "$out" "https://arxiv.org/pdf/${id}" 2>/dev/null
  if [ -s "$out" ] && head -c 5 "$out" | grep -q '%PDF'; then
    printf "  + %-24s %-14s %6sKB\n" "$name" "$klass" "$(( $(wc -c < "$out") / 1024 ))"
    got=$((got+1))
  else
    rm -f "$out"; failed="$failed $name"; echo "  ! $name FAILED"
  fi
  sleep 2
done <<EOF
$PAPERS
EOF

echo
echo "fetched $got/$n"
[ -n "$failed" ] && echo "failed:$failed"
ls -1 "$DEST"/*.pdf 2>/dev/null | wc -l | xargs echo "corpus size:"
