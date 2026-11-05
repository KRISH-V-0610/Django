from django.db import models

# Create your models here.


class Order(models.Model):
  
  product_name = models.CharField(max_length=100)
  
  amount = models.DecimalField(max_digits=10, decimal_places=2)
  created_at = models.DateTimeField(auto_now_add=True)
  
  def __str__(self): 
    return f"Order {self.id}: {self.product_name} - ${self.amount}"