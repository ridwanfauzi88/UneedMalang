'use strict';

/**
 * Client-side display helpers for DSS diagnostics. Authoritative recommendation
 * scores are computed by app_user.py so they use the developer's shared config.
 */
function calcBayesian(reviews, rating, m, c) {
  const v = Number(reviews);
  const r = Number(rating);
  const priorWeight = Number(m);
  const priorMean = Number(c);
  if (![v, r, priorWeight, priorMean].every(Number.isFinite) || v < 0 || r < 0 || r > 5 || priorWeight <= 0 || priorMean < 0 || priorMean > 5) return null;
  return ((v / (v + priorWeight)) * r) + ((priorWeight / (v + priorWeight)) * priorMean);
}

function recalculateSAW() {
  const priceInput = document.getElementById('w-price');
  const distanceInput = document.getElementById('w-dist');
  const ratingInput = document.getElementById('w-rating');
  if (!priceInput || !distanceInput || !ratingInput) return;

  const weights = [priceInput, distanceInput, ratingInput].map((input) => Number(input.value));
  if (!weights.every((value) => Number.isFinite(value) && value >= 0) || Math.abs(weights.reduce((a, b) => a + b, 0) - 100) > 0.01) return;

  [['val-price', weights[0]], ['val-dist', weights[1]], ['val-rating', weights[2]]].forEach(([id, value]) => {
    const element = document.getElementById(id);
    if (element) element.textContent = String(Math.round(value));
  });
  // No local ranking calculation is performed here: the backend must remain
  // the source of truth to prevent frontend/backend score divergence.
}
