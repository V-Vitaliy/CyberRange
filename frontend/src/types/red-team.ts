export interface Lab {
  id: string;
  title: string;
  description: string;
  isCompleted: boolean;
  difficulty: 'Easy' | 'Medium' | 'Hard';
}

export interface RedTeamState {
  currentLabs: Lab[];
  score: number;
  flagsFound: number;
}