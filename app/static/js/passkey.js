/**
 * Shekel Budget App -- Signing In and Re-confirming With a Passkey
 *
 * A passkey signs in on its own (plan#2538), and re-confirms a signed-in
 * account before a sensitive change (/reauth, beside the password and code,
 * ruling plan#3541).  For either, this module asks the server for the
 * ceremony's options, has the device sign them when the person unlocks it
 * with Face ID, a fingerprint or its passcode, and posts the device's answer
 * in the page's passkey form, which the server checks and answers with a
 * redirect, as the password form is answered.  The two forms differ only in
 * what FORMS below says of them.
 *
 * Two ways in, on the sign-in page:
 *
 *   - the email box's autofill: where the browser offers it (conditional
 *     mediation), a ceremony waits for the person to choose a passkey from
 *     the box's suggestions (the box carries autocomplete="username
 *     webauthn");
 *   - the "Sign in with a passkey" button, which stops that ceremony and
 *     shows the device's own sheet.
 *
 * The re-confirm page has the button alone ("Re-confirm with your passkey"),
 * and its options name the account's own passkeys, so the device offers no
 * one else's: the server begins no re-confirm for an account holding none.
 *
 * Both use ONE set of options, fetched before either is needed: the server
 * keeps one ceremony per session, and Safari shows the passkey sheet only
 * when it is asked inside the person's tap, so the button cannot wait for a
 * fetch first.  The button is disabled while the options are fetched and
 * while its own ceremony is being answered, and nothing fetches new options
 * then: they would replace, on the server, the ceremony the person is
 * answering.  Each ceremony is given only what is left of its challenge's
 * life, so the device gives up before the server would refuse its answer.
 *
 * A challenge lives five minutes and a waiting autofill ceremony never gives
 * up of its own accord, so the sign-in page fetches the options again once
 * their timeout has passed (or on return to a page that was hidden then).
 * The page's form token lives an hour, so once a refresh is refused for it
 * (a 400, which only the form token's check answers there) the page reloads,
 * which brings a new token and new options.  So a sign-in page left open
 * still signs in at the first try.  What a refresh cannot help is a passkey
 * that signed in on another device since these options were fetched: a
 * challenge older than its last use is refused, once, and the retry has new
 * options.
 *
 * The re-confirm page never fetches on a timer: it is signed in, so each
 * request it sent would be the session's activity, and a page left open and
 * unattended would never reach the twelve-hour idle sign-out (ruling
 * plan#3474).  It fetches when the page opens, on return to a page that was
 * hidden once the options in hand have lapsed (or none are held), and at a
 * tap that finds them lapsed, which says so and fetches, so the next tap has
 * them.  An options request the server
 * redirected (to the sign-in page, once the session has ended) reloads the
 * page, which then shows that page.
 *
 * WebAuthn wants bytes where its JSON carries unpadded base64url text, both
 * ways.  This module converts them itself rather than through
 * PublicKeyCredential.parseRequestOptionsFromJSON() and toJSON(), which not
 * every browser or passkey manager has.
 *
 * No inline script (CSP): the page says everything by data-* attributes on
 * its passkey form, data-passkey-sign-in or data-passkey-re-confirm:
 *
 *   <form data-passkey-sign-in data-options-url="...">
 *     <input name="csrf_token">       also sent with the options' request
 *     <input name="credential">       where the device's answer is written
 *     <button data-action="passkey-sign-in">   ("passkey-re-confirm")
 *     <div data-passkey-message hidden>   where a plain message is shown
 */

(function () {
  "use strict";

  /**
   * The two passkey forms, and what each does that the other does not.
   *
   *   attribute  the form's own data-* attribute;
   *   action     its button's data-action;
   *   autofill   whether a ceremony waits on the email box's autofill (the
   *              sign-in page's alone has the box);
   *   timed      whether the options are fetched again on a timer: never on
   *              a signed-in page, where a request is the session's activity;
   *   messages   what the person is told, in plain words, when it cannot go
   *              on.
   */
  var FORMS = [
    {
      attribute: "data-passkey-sign-in",
      action: "passkey-sign-in",
      autofill: true,
      timed: true,
      messages: {
        unsupported: "This browser cannot use a passkey. " +
          "Please sign in with your email and password.",
        cancelled: "The passkey sign-in did not finish. " +
          "You can try again, or sign in with your email and password.",
        failed: "The passkey sign-in could not start. " +
          "Please try again, or sign in with your email and password."
      }
    },
    {
      attribute: "data-passkey-re-confirm",
      action: "passkey-re-confirm",
      autofill: false,
      timed: false,
      messages: {
        unsupported: "This browser cannot use a passkey. " +
          "Please re-enter your password instead.",
        cancelled: "Re-confirming with your passkey did not finish. " +
          "You can try again, or re-enter your password.",
        failed: "Your passkey could not be asked just then. " +
          "Please try again, or re-enter your password."
      }
    }
  ];

  /**
   * Return the bytes unpadded base64url *text* holds.
   *
   * @param {string} text  Base64url text, as WebAuthn's JSON writes binary.
   * @returns {ArrayBuffer} Its bytes.
   */
  function toBytes(text) {
    var base64 = text.replace(/-/g, "+").replace(/_/g, "/");
    while (base64.length % 4 !== 0) {
      base64 += "=";
    }
    var binary = window.atob(base64);
    var bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) {
      bytes[i] = binary.charCodeAt(i);
    }
    return bytes.buffer;
  }

  /**
   * Return *buffer* as unpadded base64url text.
   *
   * @param {ArrayBuffer} buffer  Bytes the device returned.
   * @returns {string} Their base64url text, which the server reads.
   */
  function toText(buffer) {
    var bytes = new Uint8Array(buffer);
    var binary = "";
    for (let i = 0; i < bytes.length; i++) {
      binary += String.fromCharCode(bytes[i]);
    }
    return window.btoa(binary)
      .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  /**
   * Return the options navigator.credentials.get() takes, from the server's JSON.
   *
   * @param {Object} json  The server's options: {publicKey: {...}}.
   * @returns {Object} The publicKey options, their binary members as bytes.
   */
  function requestOptions(json) {
    var publicKey = Object.assign({}, json.publicKey);
    publicKey.challenge = toBytes(publicKey.challenge);
    if (publicKey.allowCredentials) {
      publicKey.allowCredentials = publicKey.allowCredentials.map(
        function (descriptor) {
          return Object.assign({}, descriptor, { id: toBytes(descriptor.id) });
        }
      );
    }
    return publicKey;
  }

  /**
   * Return the device's answer as the JSON the server reads.
   *
   * Both ids are written from rawId, so they are the same text whatever the
   * browser put in id.  Shekel asks for no extension, so none is reported.
   *
   * @param {PublicKeyCredential} credential  What navigator.credentials.get()
   *   resolved to.
   * @returns {string} The answer's JSON.
   */
  function answerJSON(credential) {
    var response = credential.response;
    var id = toText(credential.rawId);
    var answer = {
      id: id,
      rawId: id,
      type: credential.type,
      response: {
        clientDataJSON: toText(response.clientDataJSON),
        authenticatorData: toText(response.authenticatorData),
        signature: toText(response.signature)
      },
      clientExtensionResults: {}
    };
    if (response.userHandle) {
      answer.response.userHandle = toText(response.userHandle);
    }
    return JSON.stringify(answer);
  }

  /**
   * Return whether this browser can use a passkey at all.
   *
   * @returns {boolean} True where WebAuthn's credentials API is present.
   */
  function supported() {
    return typeof window.PublicKeyCredential === "function" &&
      Boolean(navigator.credentials) &&
      typeof navigator.credentials.get === "function";
  }

  /**
   * Wire one passkey form: its options, any waiting ceremony and its button.
   *
   * @param {HTMLFormElement} form  The form carrying kind.attribute.
   * @param {Object} kind  Its entry in FORMS.
   */
  function init(form, kind) {
    var button = form.querySelector('[data-action="' + kind.action + '"]');
    var message = form.querySelector("[data-passkey-message]");
    var field = form.elements.namedItem("credential");
    var token = form.elements.namedItem("csrf_token").value;

    // The options in hand: {options, asked}, asked being when they were
    // requested, so their age is never under-counted.  null while none are.
    var ready = null;
    // The fetch of options in flight, so two callers share one.
    var fetching = null;
    // Whether options have ever arrived on this page: a refusal after that
    // is the page's form token lapsing, never a page that could not start.
    var arrived = false;
    // The waiting autofill ceremony's AbortController, or null.
    var waiting = null;
    // Whether the button's own ceremony is being answered.
    var asking = false;
    // Whether the browser offers passkeys from the email box's autofill.
    var autofill = false;
    // Set once an answer is posted: nothing begins after it.
    var submitted = false;

    function say(text) {
      message.textContent = text;
      message.hidden = false;
    }

    function age() {
      return Date.now() - ready.asked;
    }

    function stale() {
      return age() >= ready.options.publicKey.timeout;
    }

    function publicKeyNow() {
      // What is left of the challenge's life, so the device gives up before
      // the server would refuse its answer.
      var publicKey = requestOptions(ready.options);
      publicKey.timeout = Math.max(0, ready.options.publicKey.timeout - age());
      return publicKey;
    }

    function stopWaiting() {
      if (waiting !== null) {
        waiting.abort();
        waiting = null;
      }
    }

    function submit(credential) {
      submitted = true;
      stopWaiting();
      button.disabled = true;
      field.value = answerJSON(credential);
      form.submit();
    }

    function wait() {
      var controller = new AbortController();
      waiting = controller;
      navigator.credentials.get({
        publicKey: publicKeyNow(),
        mediation: "conditional",
        signal: controller.signal
      }).then(function (credential) {
        if (waiting === controller && credential) {
          submit(credential);
        }
      }, function () {
        // Stopped by this module, or refused: the person never asked for
        // this ceremony, so it says nothing, and the button still works.
        if (waiting === controller) {
          waiting = null;
        }
      });
    }

    function refresh() {
      // Never while the button's ceremony is answered: new options would
      // replace it on the server.  A fetch that failed is tried again too.
      if (submitted || asking || document.hidden) {
        return;
      }
      if (ready === null ? fetching === null : stale()) {
        prepare();
      }
    }

    function prepare() {
      if (fetching !== null || asking || submitted) {
        return;
      }
      stopWaiting();
      ready = null;
      button.disabled = true;
      var asked = Date.now();
      fetching = fetch(form.dataset.optionsUrl, {
        method: "POST",
        headers: { "X-CSRFToken": token, "Accept": "application/json" },
        credentials: "same-origin"
      }).then(function (response) {
        if (response.redirected) {
          // The signed-in page's session has ended: the login gate sent the
          // request to sign in, and a reload shows that page.
          window.location.reload();
          throw new Error("passkey options were redirected");
        }
        if (!response.ok) {
          if (response.status === 400 && arrived) {
            // The form token has lapsed: a reload brings a new one, and
            // the sign-in form would be refused for it too.
            window.location.reload();
          }
          throw new Error("passkey options answered " + response.status);
        }
        return response.json();
      }).then(function (options) {
        fetching = null;
        if (submitted) {
          return;
        }
        arrived = true;
        ready = { options: options, asked: asked };
        button.disabled = false;
        if (kind.timed) {
          window.setTimeout(refresh, options.publicKey.timeout);
        }
        if (autofill) {
          wait();
        }
      }, function () {
        // The button is left on: a tap says so and asks again.
        fetching = null;
        button.disabled = false;
      });
    }

    button.addEventListener("click", function () {
      message.hidden = true;
      if (!supported()) {
        say(kind.messages.unsupported);
        return;
      }
      if (asking) {
        return;
      }
      if (ready === null || stale()) {
        say(kind.messages.failed);
        prepare();
        return;
      }
      stopWaiting();
      asking = true;
      button.disabled = true;
      // Asked here, inside the tap, with nothing awaited first: Safari shows
      // the passkey sheet only in answer to the person's own gesture.
      navigator.credentials.get({ publicKey: publicKeyNow() })
        .then(function (credential) {
          if (!credential) {
            throw new Error("no passkey was chosen");
          }
          asking = false;
          submit(credential);
        })
        .catch(function (error) {
          asking = false;
          button.disabled = false;
          say(error && error.name === "NotAllowedError"
            ? kind.messages.cancelled : kind.messages.failed);
          // The same options serve the next try while they live: the
          // server's ceremony was never answered.
          if (stale()) {
            prepare();
          } else if (autofill) {
            wait();
          }
        });
    });

    if (!supported()) {
      return;
    }
    document.addEventListener("visibilitychange", refresh);
    if (!kind.autofill) {
      prepare();
      return;
    }
    var offered = typeof PublicKeyCredential.isConditionalMediationAvailable === "function"
      ? PublicKeyCredential.isConditionalMediationAvailable()
      : Promise.resolve(false);
    offered.then(function (available) {
      autofill = available === true;
    }, function () {
      autofill = false;
    }).then(prepare);
  }

  function initAll() {
    FORMS.forEach(function (kind) {
      var forms = document.querySelectorAll("form[" + kind.attribute + "]");
      for (let i = 0; i < forms.length; i++) {
        init(forms[i], kind);
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initAll);
  } else {
    initAll();
  }
})();
