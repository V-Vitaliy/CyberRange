export interface DefenseOption {
  id: string;
  name: string;
  cost: number;
  enabled: boolean;
  description: string;
}

export interface SiemEvent {
  id: string;
  timestamp: string;
  event_type: string;
  payload: any;
  investigated_at: string | null;
}