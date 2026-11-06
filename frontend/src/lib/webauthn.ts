function b64ToBuf(value: string): ArrayBuffer {
  const pad = value.replace(/-/g, "+").replace(/_/g, "/");
  const binary = atob(pad + "=".repeat((4 - (pad.length % 4)) % 4));
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  return bytes.buffer;
}

function bufToB64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

type JsonCredential = {
  id: string;
  type?: string;
  transports?: string[];
};

export function creationOptions(publicKey: Record<string, unknown>): PublicKeyCredentialCreationOptions {
  const user = publicKey.user as { id: string; name: string; displayName: string };
  const exclude = (publicKey.excludeCredentials || []) as JsonCredential[];
  return {
    ...(publicKey as unknown as PublicKeyCredentialCreationOptions),
    challenge: b64ToBuf(String(publicKey.challenge)),
    user: { ...user, id: b64ToBuf(user.id) },
    excludeCredentials: exclude.map((item) => ({
      type: "public-key",
      id: b64ToBuf(item.id),
      transports: item.transports as AuthenticatorTransport[] | undefined,
    })),
  };
}

export function requestOptions(publicKey: Record<string, unknown>): PublicKeyCredentialRequestOptions {
  const allow = (publicKey.allowCredentials || []) as JsonCredential[];
  return {
    ...(publicKey as unknown as PublicKeyCredentialRequestOptions),
    challenge: b64ToBuf(String(publicKey.challenge)),
    allowCredentials: allow.map((item) => ({
      type: "public-key",
      id: b64ToBuf(item.id),
      transports: item.transports as AuthenticatorTransport[] | undefined,
    })),
  };
}

export function credentialToJson(credential: PublicKeyCredential): Record<string, unknown> {
  const response = credential.response as AuthenticatorAttestationResponse & AuthenticatorAssertionResponse;
  const result: Record<string, unknown> = {
    id: credential.id,
    rawId: bufToB64(credential.rawId),
    type: credential.type,
    clientExtensionResults: credential.getClientExtensionResults(),
    authenticatorAttachment: credential.authenticatorAttachment,
    response: {
      clientDataJSON: bufToB64(response.clientDataJSON),
    },
  };
  const body = result.response as Record<string, unknown>;
  if ("attestationObject" in response && response.attestationObject) {
    body.attestationObject = bufToB64(response.attestationObject);
    body.transports = typeof response.getTransports === "function" ? response.getTransports() : [];
  } else {
    body.authenticatorData = bufToB64(response.authenticatorData);
    body.signature = bufToB64(response.signature);
    if (response.userHandle) body.userHandle = bufToB64(response.userHandle);
  }
  return result;
}
