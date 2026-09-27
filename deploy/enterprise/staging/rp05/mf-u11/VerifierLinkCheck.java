package win.clannad.mentorflow.check;

/** Offline class linkage check; neither database nor realm is contacted. */
public final class VerifierLinkCheck {
    public static void main(String[] args) throws Exception {
        for (String name : new String[]{"NativeIdentityVerifier", "RegistrationCodes", "EmailCodeReset",
                "EmailCodeBinding", "AtomicActionTokens", "ResetCompletions", "MailTransportHealth"}) {
            Class<?> type=Class.forName("win.clannad.mentorflow."+name, false, VerifierLinkCheck.class.getClassLoader());
            type.getDeclaredMethods(); type.getDeclaredConstructors();
        }
        System.out.println("Keycloak verifier class linkage: PASS");
    }
}
