import { AbstractControl, ValidationErrors, ValidatorFn } from '@angular/forms';

/**
 * Date du jour au format des champs `date` (« 2026-10-22 »).
 *
 * Date locale, pas `toISOString()` : en UTC, une saisie faite tard le soir
 * tomberait sur le lendemain dans les fuseaux à l'est de Greenwich.
 */
export function dateDuJour(): string {
  const d = new Date();
  const deux = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${deux(d.getMonth() + 1)}-${deux(d.getDate())}`;
}

/**
 * Instant à envoyer pour un paiement daté au jour près.
 *
 * Aujourd'hui (ou rien) : on n'envoie rien, le serveur horodate à la minute
 * de l'encaissement — le journal de caisse garde ainsi l'ordre des passages.
 * Un autre jour : midi ce jour-là, l'heure exacte n'étant pas connue.
 */
export function instantDuPaiement(date: string | null | undefined): string | undefined {
  return date && date !== dateDuJour() ? `${date}T12:00:00` : undefined;
}

/**
 * Refuse une date à venir : un paiement ou une dépense se date du jour où
 * il a eu lieu. Le sélecteur la grise déjà (`max`), mais une date tapée au
 * clavier passe outre.
 */
export const pasDansLeFutur: ValidatorFn = (controle: AbstractControl): ValidationErrors | null => {
  const valeur = controle.value;
  return valeur && String(valeur) > dateDuJour() ? { dateFuture: true } : null;
};
