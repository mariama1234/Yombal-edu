/** Comment lire une valeur de graphique : un effectif, ou des francs CFA. */
export type FormatValeur = 'nombre' | 'montant';

const NOMBRE = new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 0 });

export function formaterValeur(valeur: number, format: FormatValeur): string {
  const texte = NOMBRE.format(valeur);
  return format === 'montant' ? `${texte} F` : texte;
}

/**
 * 1 250 000 → « 1,3 M » ; 84 000 → « 84 k ». Un axe ou un centre d'anneau
 * en unités brutes déborde et se lit mal.
 */
export function abregerMontant(valeur: number): string {
  const absolu = Math.abs(valeur);
  if (absolu >= 1_000_000) {
    const millions = valeur / 1_000_000;
    return `${millions.toLocaleString('fr-FR', { maximumFractionDigits: absolu >= 10_000_000 ? 0 : 1 })} M`;
  }
  if (absolu >= 1_000) return `${Math.round(valeur / 1_000)} k`;
  return String(Math.round(valeur));
}

/**
 * Pas rond des quatre intervalles de l'axe : 1, 2, 2,5 ou 5 × 10ⁿ.
 * Un effectif n'a pas de demi-élève : `entier` écarte les pas fractionnaires.
 */
export function pasDeGraduation(maximum: number, entier: boolean): number {
  const brut = Math.max(maximum, entier ? 4 : 1) / 4;
  const puissance = Math.pow(10, Math.floor(Math.log10(brut)));
  for (const facteur of [1, 2, 2.5, 5, 10]) {
    const pas = facteur * puissance;
    if (entier && !Number.isInteger(pas)) continue;
    if (pas >= brut) return pas;
  }
  return 10 * puissance;
}
