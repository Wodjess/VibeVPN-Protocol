import AsyncStorage from '@react-native-async-storage/async-storage';

const SERVERS_KEY = 'vibevpn_servers';

export async function loadServers() {
  try {
    const data = await AsyncStorage.getItem(SERVERS_KEY);
    return data ? JSON.parse(data) : [];
  } catch { return []; }
}

export async function saveServers(servers) {
  await AsyncStorage.setItem(SERVERS_KEY, JSON.stringify(servers));
}
