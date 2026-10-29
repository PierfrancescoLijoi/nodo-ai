export type ConnectionMode = 'mock' | 'local' | 'azure';

export interface AzureProfile {
  apiBaseUrl: string;
  issuer: string;
  clientId: string;
  apiScope: string;
}

export type ResolvedConnection =
  | { mode: 'mock' }
  | { mode: 'local'; apiBaseUrl: string; token: string }
  | { mode: 'azure'; profile: AzureProfile; source?: 'profile' | 'build'; devToken?: string }
  | { mode: 'setup' };
