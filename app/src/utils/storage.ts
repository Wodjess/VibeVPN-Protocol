import AsyncStorage from '@react-native-async-storage/async-storage';

const SERVERS_KEY = 'vibevpn_servers';

export type Server = {
  id: string;
  name: string;
  host: string;
  port: number;
  username: string;
  password: string;
};

export async function loadServers(): Promise<Server[]> {
  try {
    const data = await AsyncStorage.getItem(SERVERS_KEY);
    return data ? JSON.parse(data) : [];
  } catch {
    return [];
  }
}

export async function saveServers(servers: Server[]): Promise<void> {
  await AsyncStorage.setItem(SERVERS_KEY, JSON.stringify(servers));
}
