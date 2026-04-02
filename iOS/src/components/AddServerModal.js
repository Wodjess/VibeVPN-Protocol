import React, { useState } from 'react';
import {
  View, Text, TextInput, TouchableOpacity, Modal, StyleSheet, KeyboardAvoidingView, Platform,
} from 'react-native';

export default function AddServerModal({ visible, onClose, onAdd }) {
  const [name, setName] = useState('');
  const [host, setHost] = useState('');
  const [port, setPort] = useState('443');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');

  const handleAdd = () => {
    if (!host || !username || !password) return;
    onAdd({ name: name || host, host, port: parseInt(port, 10) || 443, username, password });
    setName(''); setHost(''); setPort('443'); setUsername(''); setPassword('');
  };

  return (
    <Modal visible={visible} animationType="slide" transparent>
      <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : 'height'} style={styles.overlay}>
        <View style={styles.modal}>
          <Text style={styles.title}>Add Server</Text>

          <TextInput style={styles.input} placeholder="Name (optional)" placeholderTextColor="#6b7280"
            value={name} onChangeText={setName} />
          <TextInput style={styles.input} placeholder="Host (e.g. vpn.example.com)" placeholderTextColor="#6b7280"
            value={host} onChangeText={setHost} autoCapitalize="none" keyboardType="url" />
          <TextInput style={styles.input} placeholder="Port (443)" placeholderTextColor="#6b7280"
            value={port} onChangeText={setPort} keyboardType="number-pad" />
          <TextInput style={styles.input} placeholder="Username" placeholderTextColor="#6b7280"
            value={username} onChangeText={setUsername} autoCapitalize="none" />
          <TextInput style={styles.input} placeholder="Password" placeholderTextColor="#6b7280"
            value={password} onChangeText={setPassword} secureTextEntry />

          <View style={styles.buttons}>
            <TouchableOpacity style={styles.cancelBtn} onPress={onClose}>
              <Text style={styles.cancelText}>Cancel</Text>
            </TouchableOpacity>
            <TouchableOpacity style={[styles.addBtnStyle, (!host || !username || !password) && styles.disabled]}
              onPress={handleAdd} disabled={!host || !username || !password}>
              <Text style={styles.addText}>Add Server</Text>
            </TouchableOpacity>
          </View>
        </View>
      </KeyboardAvoidingView>
    </Modal>
  );
}

const styles = StyleSheet.create({
  overlay: { flex: 1, justifyContent: 'flex-end', backgroundColor: 'rgba(0,0,0,0.6)' },
  modal: { backgroundColor: '#161921', borderTopLeftRadius: 20, borderTopRightRadius: 20, padding: 24 },
  title: { fontSize: 18, fontWeight: '700', color: '#e4e7ed', marginBottom: 16 },
  input: { backgroundColor: '#232830', borderRadius: 10, padding: 14, color: '#e4e7ed', fontSize: 15, marginBottom: 10 },
  buttons: { flexDirection: 'row', justifyContent: 'space-between', marginTop: 12 },
  cancelBtn: { flex: 1, padding: 14, alignItems: 'center' },
  cancelText: { color: '#6b7280', fontSize: 15, fontWeight: '600' },
  addBtnStyle: { flex: 1, padding: 14, backgroundColor: '#22c55e', borderRadius: 10, alignItems: 'center' },
  addText: { color: '#fff', fontSize: 15, fontWeight: '700' },
  disabled: { opacity: 0.4 },
});
