import { Component, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { AuthService } from '../core/services/auth.service';

@Component({
  selector: 'app-layout',
  standalone: true,
  imports: [CommonModule, RouterLink, RouterLinkActive, RouterOutlet],
  templateUrl: './layout.component.html',
  styleUrl: './layout.component.scss',
})
export class LayoutComponent {
  constructor(public auth: AuthService) {}

  initiales = computed(() => {
    const u = this.auth.currentUser();
    if (!u) return '';
    return `${u.prenom[0] ?? ''}${u.nom[0] ?? ''}`.toUpperCase();
  });

  logout(): void {
    this.auth.logout();
  }
}
