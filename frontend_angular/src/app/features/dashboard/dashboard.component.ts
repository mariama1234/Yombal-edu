import { Component, OnInit, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';
import { HttpClient } from '@angular/common/http';
import { environment } from '../../../environments/environment';
import { AuthService } from '../../core/services/auth.service';
import { Bilan, Paginated } from '../../core/models/models';
import { FinanceService } from '../finances/finance.service';

@Component({
  selector: 'app-dashboard',
  standalone: true,
  imports: [CommonModule, RouterLink],
  templateUrl: './dashboard.component.html',
})
export class DashboardComponent implements OnInit {
  private http = inject(HttpClient);
  private finance = inject(FinanceService);
  auth = inject(AuthService);

  nbEleves = signal<number | null>(null);
  nbClasses = signal<number | null>(null);
  bilan = signal<Bilan | null>(null);

  ngOnInit(): void {
    this.http
      .get<Paginated<unknown>>(`${environment.apiUrl}/eleves/`)
      .subscribe((r) => this.nbEleves.set(r.count));
    this.http
      .get<Paginated<unknown>>(`${environment.apiUrl}/classes/`)
      .subscribe((r) => this.nbClasses.set(r.count));

    // Le bilan porte déjà les chiffres financiers de l'année active : une
    // seule requête remplace le comptage des impayés et le solde de caisse.
    this.finance.bilan().subscribe({
      next: (b) => this.bilan.set(b),
      error: () => {},
    });
  }
}
