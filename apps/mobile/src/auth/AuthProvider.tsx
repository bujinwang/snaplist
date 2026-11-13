/**
 * Auth provider — E6-T1.
 *
 * First launch signs in *anonymously* (ADR-003): a seller can capture and export before any
 * email is asked for. When Supabase env is configured we use `@supabase/supabase-js`; otherwise
 * we run a mock anonymous session so the app is fully usable (and every screen renders) in mock
 * mode.
 *
 * The client is created only when `hasSupabase` is true, so mock mode never constructs one.
 *
 * NOTE (React Native session storage): supabase-js persists sessions through `localStorage` by
 * default, which RN does not provide. Without an explicit `storage` adapter it falls back to
 * memory, so the anonymous subject is re-created on each launch. Wiring
 * `@react-native-async-storage/async-storage` is the follow-up; it is not added here because it
 * is a native module.
 */

import React, { createContext, useContext, useEffect, useRef, useState } from "react";
import { createClient, type SupabaseClient } from "@supabase/supabase-js";

import { config, hasSupabase } from "../lib/env";
import { uuidv4 } from "../lib/uuid";
import { configureAnalytics } from "../lib/analytics";

function createSupabaseClient(): SupabaseClient | null {
  if (!hasSupabase) return null;
  try {
    return createClient(config.supabaseUrl, config.supabaseAnonKey, {
      auth: { persistSession: true, autoRefreshToken: true },
    });
  } catch {
    return null;
  }
}

export interface AuthState {
  ready: boolean;
  /** `anon_<uuid>` before signup, `usr_<uuid>` after identity. Never an email. */
  subject: string;
  userId: string | null;
  isAnon: boolean;
  signInWithMagicLink: (email: string) => Promise<void>;
  signInWithApple: () => Promise<void>;
  signInWithGoogle: () => Promise<void>;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within <AuthProvider>");
  return ctx;
}

function mockIdentify(current: string | null): { subject: string; userId: string; isAnon: boolean } {
  const id = current ?? uuidv4();
  return { subject: `usr_${id}`, userId: id, isAnon: false };
}

export function AuthProvider(props: { children: React.ReactNode }): JSX.Element {
  const { children } = props;
  const [ready, setReady] = useState(false);
  const [subject, setSubject] = useState("anon_unknown");
  const [userId, setUserId] = useState<string | null>(null);
  const [isAnon, setIsAnon] = useState(true);
  const clientRef = useRef<SupabaseClient | null>(null);
  const sessionIdRef = useRef<string>(uuidv4());

  useEffect(() => {
    let active = true;
    void (async () => {
      const client = createSupabaseClient();
      clientRef.current = client;
      let resolvedSubject = "";
      let resolvedUserId: string | null = null;
      let resolvedIsAnon = true;

      if (client) {
        const existing = await client.auth.getUser();
        if (existing.data.user) {
          resolvedSubject = `usr_${existing.data.user.id}`;
          resolvedUserId = existing.data.user.id;
          resolvedIsAnon = false;
        } else {
          const anon = await client.auth.signInAnonymously();
          if (anon.data.user && !anon.error) {
            resolvedSubject = `anon_${anon.data.user.id}`;
            resolvedUserId = anon.data.user.id;
            resolvedIsAnon = true;
          }
        }
      }

      if (!resolvedSubject) {
        const id = uuidv4();
        resolvedSubject = `anon_${id}`;
        resolvedUserId = id;
        resolvedIsAnon = true;
      }

      if (!active) return;
      setSubject(resolvedSubject);
      setUserId(resolvedUserId);
      setIsAnon(resolvedIsAnon);
      configureAnalytics({ subject: resolvedSubject, sessionId: sessionIdRef.current });
      setReady(true);
    })();
    return () => {
      active = false;
    };
  }, []);

  const applyIdentity = (next: { subject: string; userId: string; isAnon: boolean }): void => {
    setSubject(next.subject);
    setUserId(next.userId);
    setIsAnon(next.isAnon);
    configureAnalytics({ subject: next.subject, sessionId: sessionIdRef.current });
  };

  const signInWithMagicLink = async (email: string): Promise<void> => {
    const client = clientRef.current;
    if (client) {
      await client.auth.signInWithOtp({ email });
      return;
    }
    applyIdentity(mockIdentify(userId));
  };

  const signInWithApple = async (): Promise<void> => {
    const client = clientRef.current;
    if (client) {
      await client.auth.signInWithOAuth({ provider: "apple" });
      return;
    }
    applyIdentity(mockIdentify(userId));
  };

  const signInWithGoogle = async (): Promise<void> => {
    const client = clientRef.current;
    if (client) {
      await client.auth.signInWithOAuth({ provider: "google" });
      return;
    }
    applyIdentity(mockIdentify(userId));
  };

  const signOut = async (): Promise<void> => {
    const client = clientRef.current;
    if (client) await client.auth.signOut();
    const id = uuidv4();
    applyIdentity({ subject: `anon_${id}`, userId: id, isAnon: true });
  };

  const value: AuthState = {
    ready,
    subject,
    userId,
    isAnon,
    signInWithMagicLink,
    signInWithApple,
    signInWithGoogle,
    signOut,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
