import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { HttpClient } from '@angular/common/http';
import { environment } from '../../../environments/environment';
import { Classe, Paginated } from '../../core/models/models';
import { AnneeScolaireService } from '../../core/services/annee-scolaire.service';
import { ToastService } from '../../core/services/toast.service';

interface LigneFiche {
  id: number;
  matricule: string;
  nom_complet: string;
  date_naissance: string | null;
  sexe: 'M' | 'F' | null;
  numero_registre: string | null;
  incomplete: boolean;
}

/** Une ligne en cours de saisie : les valeurs d'origine, et celles du formulaire. */
interface LigneSaisie extends LigneFiche {
  saisie: { date_naissance: string; sexe: string; numero_registre: string };
}

/**
 * Compléter les fiches d'une classe : date de naissance, sexe, n° de registre.
 *
 * Les listes importées n'ont ni date de naissance ni sexe. Sans date, l'âge
 * ne se contrôle pas et la réinscription s'arrête ; ouvrir cinq cents
 * dossiers un par un n'est pas une option. Ici, une classe entière se
 * complète d'un envoi.
 */
@Component({
  selector: 'app-completer-fiches',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink],
  templateUrl: './completer-fiches.component.html',
})
export class CompleterFichesComponent implements OnInit {
  private http = inject(HttpClient);
  private route = inject(ActivatedRoute);
  private router = inject(Router);
  private toast = inject(ToastService);
  annees = inject(AnneeScolaireService);

  classes = signal<Classe[]>([]);
  idClasse = signal<number | null>(null);
  lignes = signal<LigneSaisie[]>([]);
  seulementIncompletes = signal(true);
  chargement = signal(false);
  occupe = signal(false);
  erreur = signal<string | null>(null);

  /** Classes regroupées par année, l'année du bandeau d'abord. */
  groupes = computed(() => {
    const courante = this.annees.idAnnee();
    const parAnnee = new Map<number, Classe[]>();
    for (const c of this.classes()) {
      parAnnee.set(c.id_annee_scolaire, [...(parAnnee.get(c.id_annee_scolaire) ?? []), c]);
    }
    return [...parAnnee.entries()]
      .map(([id, classes]) => ({
        id,
        libelle: this.annees.annees().find((a) => a.id === id)?.libelle ?? `Année ${id}`,
        classes: classes.sort((a, b) => a.nom.localeCompare(b.nom)),
      }))
      .sort((a, b) => (a.id === courante ? -1 : b.id === courante ? 1 : b.libelle.localeCompare(a.libelle)));
  });

  visibles = computed(() =>
    this.seulementIncompletes() ? this.lignes().filter((l) => l.incomplete) : this.lignes());

  nbIncompletes = computed(() => this.lignes().filter((l) => l.incomplete).length);
  modifiees = computed(() => this.lignes().filter((l) => this.changee(l)));

  ngOnInit(): void {
    this.http.get<Paginated<Classe>>(`${environment.apiUrl}/classes/?page_size=500`)
      .subscribe((r) => this.classes.set(r.results));
    const classe = Number(this.route.snapshot.queryParamMap.get('classe'));
    if (classe) this.choisirClasse(classe);
  }

  choisirClasse(id: number | null): void {
    this.idClasse.set(id);
    this.lignes.set([]);
    this.erreur.set(null);
    this.router.navigate([], { queryParams: { classe: id }, replaceUrl: true });
    if (id) this.charger();
  }

  charger(): void {
    const id = this.idClasse();
    if (!id) return;
    this.chargement.set(true);
    this.http.get<{ eleves: LigneFiche[] }>(`${environment.apiUrl}/eleves/fiches/`, { params: { classe: id } })
      .subscribe({
        next: (r) => {
          this.lignes.set(r.eleves.map((e) => ({
            ...e,
            saisie: {
              date_naissance: e.date_naissance ?? '',
              sexe: e.sexe ?? '',
              numero_registre: e.numero_registre ?? '',
            },
          })));
          this.chargement.set(false);
        },
        error: (e) => {
          this.erreur.set(e?.error?.classe ?? e?.error?.detail ?? 'La classe n’a pas pu être chargée.');
          this.chargement.set(false);
        },
      });
  }

  changee(l: LigneSaisie): boolean {
    return l.saisie.date_naissance !== (l.date_naissance ?? '')
      || l.saisie.sexe !== (l.sexe ?? '')
      || l.saisie.numero_registre.trim() !== (l.numero_registre ?? '');
  }

  /** Une saisie touche le signal : les compteurs suivent. */
  modifier(l: LigneSaisie, champ: keyof LigneSaisie['saisie'], valeur: string): void {
    l.saisie[champ] = valeur;
    this.lignes.update((lignes) => [...lignes]);
  }

  enregistrer(): void {
    const lignes = this.modifiees();
    if (!lignes.length) return;
    this.occupe.set(true);
    this.erreur.set(null);
    const eleves = lignes.map((l) => ({
      id: l.id,
      date_naissance: l.saisie.date_naissance || null,
      sexe: l.saisie.sexe || null,
      numero_registre: l.saisie.numero_registre.trim() || null,
    }));
    this.http.post<{ modifies: number }>(`${environment.apiUrl}/eleves/fiches/`, { eleves }).subscribe({
      next: (r) => {
        this.occupe.set(false);
        this.toast.succes(`${r.modifies} fiche(s) enregistrée(s)`);
        this.charger();
      },
      error: (e) => {
        this.occupe.set(false);
        const corps = e?.error;
        this.erreur.set(typeof corps?.detail === 'string' ? corps.detail : 'L’enregistrement a échoué.');
      },
    });
  }
}
