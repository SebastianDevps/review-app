import type { NextAuthOptions } from "next-auth";
import GithubProvider from "next-auth/providers/github";

export const authOptions: NextAuthOptions = {
  providers: [
    GithubProvider({
      clientId: process.env.GITHUB_OAUTH_CLIENT_ID!,
      clientSecret: process.env.GITHUB_OAUTH_CLIENT_SECRET!,
    }),
  ],
  pages: {
    signIn: "/",
  },
  callbacks: {
    async session({ session, token }) {
      if (session.user && token.login) {
        (session.user as { login?: string }).login = token.login as string;
      }
      return session;
    },
    async jwt({ token, profile }) {
      if (profile) {
        token.login = (profile as { login?: string }).login;
      }
      return token;
    },
  },
};
