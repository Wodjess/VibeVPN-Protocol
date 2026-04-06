export type VpnStatus = 'disconnected' | 'connecting' | 'connected' | 'disconnecting';

export type VpnConfig = {
  host: string;
  port: number;
  username: string;
  password: string;
};

export type VpnStatusResult = {
  status: VpnStatus;
  peers: string[];
};

export type VpnStatusChangeEvent = {
  status: VpnStatus;
};
