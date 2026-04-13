export interface AuthProfileLike {
  display_name?: string | null;
  login_name?: string | null;
  avatar_url?: string | null;
}

export function getUserDisplayLabel(user: AuthProfileLike | null | undefined) {
  return user?.display_name || user?.login_name || "Local User";
}

export function getUserDisplayAvatarUrl(user: AuthProfileLike | null | undefined) {
  const avatarUrl = user?.avatar_url?.trim();
  return avatarUrl ? avatarUrl : null;
}
