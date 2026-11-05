"""What a Stripe failure means to a caller."""

import stripe

ProviderError = stripe.StripeError


def refusal(error: ProviderError) -> tuple[int, str]:
    """A declined card is the customer's to fix; anything else is Stripe's, or a setting of ours."""
    status = 402 if isinstance(error, stripe.CardError) else 502
    return status, error.user_message or "Stripe refused the request"
