import { NativeModules, NativeEventEmitter } from 'react-native';

const { VPNManager } = NativeModules;
const vpnEmitter = new NativeEventEmitter(VPNManager);

export function connect({ host, port, username, password }) {
  return VPNManager.connect({ host, port, username, password });
}

export function disconnect() {
  return VPNManager.disconnect();
}

export function getStatus() {
  return VPNManager.getStatus();
}

export function onStatusChange(callback) {
  const sub = vpnEmitter.addListener('vpnStatusChanged', callback);
  return () => sub.remove();
}
