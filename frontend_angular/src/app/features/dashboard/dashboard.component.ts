import { Component, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';
import { HttpClient } from '@angular/common/http';
import { environment } from '../../../environments/environment';
import { AuthService } from '../../core/services/auth.service';
import { Paginated } from '../../core/models/models';

@Component({
  selector: 'app-dashboard',
  standalone: true,
  imports: [CommonModule, RouterLink],
  templateUrl: './dashboard.component.html',
})
export class DashboardComponent implements OnInit {
  nbEleves = signal<number | null>(null);
  nbClasses = signal<number | null>(null);
  nbImpayes = signal<number | null>(null);

  constructor(private http: HttpClient, public auth: AuthService) {}

  ngOnInit(): void {
    this.http.get<Paginated<any>>(`${environment.apiUrl}/eleves/`).subscribe((r) => this.nbEleves.set(r.count));
    this.http.get<Paginated<any>>(`${environment.apiUrl}/classes/`).subscribe((r) => this.nbClasses.set(r.count));
    this.http.get<Paginated<any>>(`${environment.apiUrl}/frais-eleves/impayes/`).subscribe((r) => this.nbImpayes.set(r.count ?? (r as any).length));
  }
}
