/**
 * Refus du serveur lié à l'âge de l'élève, à l'inscription comme à la
 * réinscription. Le `code` de la réponse 400 dit quoi faire :
 *
 * - `age_a_confirmer` : simple avertissement, on peut renvoyer la requête
 *   avec `derogation_age: true` (la dérogation est tracée dans l'historique) ;
 * - `age_hors_fourchette` : l'école bloque, rien à confirmer ;
 * - `date_naissance_manquante` : il faut la saisir.
 */
export interface AlerteAge {
  code: 'age_a_confirmer' | 'age_hors_fourchette' | 'date_naissance_manquante';
  message: string;
  confirmable: boolean;
}

const CODES = ['age_a_confirmer', 'age_hors_fourchette', 'date_naissance_manquante'];

export function alerteAge(erreur: unknown): AlerteAge | null {
  const corps = (erreur as { error?: Record<string, unknown> })?.error;
  const code = corps?.['code'];
  if (typeof code !== 'string' || !CODES.includes(code)) return null;
  const message = String(corps?.['detail'] ?? corps?.['date_naissance'] ?? '');
  return { code: code as AlerteAge['code'], message, confirmable: code === 'age_a_confirmer' };
}

/** « de 12 à 14 ans », « à partir de 3 ans »… ou null sans fourchette. */
export function fourchetteLisible(niveau: { age_min?: number | null; age_max?: number | null } | null): string | null {
  if (!niveau) return null;
  const { age_min: min, age_max: max } = niveau;
  if (min != null && max != null) return `de ${min} à ${max} ans`;
  if (min != null) return `à partir de ${min} ans`;
  if (max != null) return `jusqu’à ${max} ans`;
  return null;
}
