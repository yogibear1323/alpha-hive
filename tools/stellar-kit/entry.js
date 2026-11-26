/**
 * Browser IIFE bundle entry for Alpha Hive.
 * Exposes window.AlphaHiveStellarKit — the Wallets Kit plus Stellar SDK pieces
 * needed to look up accounts and build a payment. No secret-key handling.
 */
import { StellarWalletsKit } from "@creit.tech/stellar-wallets-kit/sdk";
import { defaultModules } from "@creit.tech/stellar-wallets-kit/modules/utils";
import { Networks, KitEventType, SwkAppDarkTheme } from "@creit.tech/stellar-wallets-kit/types";
import {
  Account,
  Asset,
  Horizon,
  Memo,
  Networks as SdkNetworks,
  Operation,
  StrKey,
  Transaction,
  TransactionBuilder,
} from "@stellar/stellar-sdk";

const hiveTheme = {
  ...SwkAppDarkTheme,
  background: "#14120f",
  "background-secondary": "#1f1b15",
  "foreground-strong": "#f4ead4",
  foreground: "#f4ead4",
  "foreground-secondary": "#b9ab8e",
  primary: "#ffb627",
  "primary-foreground": "#1a1206",
  "light-gray": "#d8ccb1",
  gray: "#b9ab8e",
  danger: "#ff6b6b",
  border: "rgba(255,182,39,0.35)",
  shadow: "0 10px 24px rgba(0,0,0,0.45)",
  "border-radius": "10px",
  "font-family": 'ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace',
};

window.AlphaHiveStellarKit = {
  StellarWalletsKit,
  defaultModules,
  Networks,
  KitEventType,
  SwkAppDarkTheme,
  hiveTheme,
  Horizon,
  Account,
  Asset,
  Memo,
  Operation,
  StrKey,
  Transaction,
  TransactionBuilder,
  SdkNetworks,
};
